//! `polynomial_positive_interval`: P > 0 (or P >= 0) on a closed interval, exactly.
//!
//! 1. clear denominators, make P primitive;
//! 2. square-free part s = P / gcd(P, P') (one prime certifies gcd = 1; otherwise the small-primes
//!    modular gcd, verified by exact division);
//! 3. each piece of the interval is mapped to y in [0, 1] by x = A(y)/B(y) (A, B linear, B > 0 on
//!    [0, 1)) and R(y) = sum_i c_i A(y)^i B(y)^(m-i) is computed by a homogeneous Horner scheme;
//! 4. Vincent-Collins-Akritas bisection isolates the roots of the transformed s in [0, 1];
//! 5. P's sign is evaluated exactly at a sample point of every root-free component.

use crate::common::{as_arr, fields, malformed, parse_rational, rational_str, Outcome, Res, Stats};
use crate::json::J;
use num_bigint::BigInt;
use num_integer::Integer;
use num_rational::BigRational;
use num_traits::{One, Signed, ToPrimitive, Zero};

const MAX_DEGREE: usize = 2000;

type Poly = Vec<BigInt>; // lowest degree first

fn strip(mut p: Poly) -> Poly {
    while p.last().is_some_and(|x| x.is_zero()) {
        p.pop();
    }
    p
}

fn primitive(p: Poly) -> Poly {
    let mut g = BigInt::zero();
    for x in &p {
        g = g.gcd(x);
        if g.is_one() {
            return p;
        }
    }
    if g.is_zero() || g.is_one() {
        return p;
    }
    p.into_iter().map(|x| x / &g).collect()
}

fn derivative(p: &[BigInt]) -> Poly {
    p.iter().enumerate().skip(1).map(|(i, x)| x * BigInt::from(i)).collect()
}

/// a / d when d divides a in Z[x] (d primitive), else None.
fn exact_div(a: &[BigInt], d: &[BigInt]) -> Option<Poly> {
    let dd = d.len() - 1;
    if a.len() < d.len() {
        return if a.iter().all(|x| x.is_zero()) {
            Some(vec![])
        } else {
            None
        };
    }
    let mut a = a.to_vec();
    let lead = &d[dd];
    let mut quo = vec![BigInt::zero(); a.len() - dd];
    for top in (dd..a.len()).rev() {
        if a[top].is_zero() {
            continue;
        }
        let (f, r) = a[top].div_rem(lead);
        if !r.is_zero() {
            return None;
        }
        for i in 0..=dd {
            let t = &f * &d[i];
            a[top - dd + i] -= t;
        }
        quo[top - dd] = f;
    }
    if a[..dd].iter().any(|x| !x.is_zero()) {
        return None;
    }
    Some(quo)
}

// ------------------------------------------------------------------------------------------
// arithmetic modulo word-sized primes
// ------------------------------------------------------------------------------------------

fn mulmod(a: u64, b: u64, p: u64) -> u64 {
    ((u128::from(a) * u128::from(b)) % u128::from(p)) as u64
}

fn powmod(mut a: u64, mut e: u64, p: u64) -> u64 {
    let mut r = 1u64;
    a %= p;
    while e > 0 {
        if e & 1 == 1 {
            r = mulmod(r, a, p);
        }
        a = mulmod(a, a, p);
        e >>= 1;
    }
    r
}

fn is_prime(n: u64) -> bool {
    if n < 2 {
        return false;
    }
    const SMALL: [u64; 12] = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37];
    for p in SMALL {
        if n.is_multiple_of(p) {
            return n == p;
        }
    }
    let (mut d, mut s) = (n - 1, 0);
    while d % 2 == 0 {
        d /= 2;
        s += 1;
    }
    'outer: for a in SMALL {
        let mut x = powmod(a, d, n);
        if x == 1 || x == n - 1 {
            continue;
        }
        for _ in 1..s {
            x = mulmod(x, x, n);
            if x == n - 1 {
                continue 'outer;
            }
        }
        return false;
    }
    true
}

struct Primes(u64);

impl Iterator for Primes {
    type Item = u64;
    fn next(&mut self) -> Option<u64> {
        loop {
            self.0 -= 1;
            if is_prime(self.0) {
                return Some(self.0);
            }
        }
    }
}

fn reduce(a: &[BigInt], p: u64) -> Vec<u64> {
    let pb = BigInt::from(p);
    let mut v: Vec<u64> = a.iter().map(|x| x.mod_floor(&pb).to_u64().unwrap()).collect();
    while v.last() == Some(&0) {
        v.pop();
    }
    v
}

fn gcd_mod(mut a: Vec<u64>, mut b: Vec<u64>, p: u64) -> Vec<u64> {
    while !b.is_empty() {
        // a <- a mod b
        let db = b.len() - 1;
        let inv = powmod(b[db], p - 2, p);
        while a.len() > db {
            let top = a.len() - 1;
            let c = mulmod(a[top], inv, p);
            if c != 0 {
                for i in 0..=db {
                    let idx = top - db + i;
                    a[idx] = (a[idx] + p - mulmod(c, b[i], p)) % p;
                }
            }
            a.pop();
            while a.last() == Some(&0) {
                a.pop();
            }
        }
        std::mem::swap(&mut a, &mut b);
    }
    let inv = powmod(*a.last().unwrap(), p - 2, p);
    a.iter().map(|&x| mulmod(x, inv, p)).collect()
}

/// gcd of nonzero integer polynomials (primitive, positive leading coefficient).
fn int_gcd(a: &[BigInt], b: &[BigInt]) -> Poly {
    let a = primitive(a.to_vec());
    let b = primitive(b.to_vec());
    let gamma = a.last().unwrap().gcd(b.last().unwrap());
    let mut best: Option<usize> = None;
    let mut residues: Vec<BigInt> = Vec::new();
    let mut modulus = BigInt::one();
    let mut previous: Option<Poly> = None;
    for p in Primes(1 << 62) {
        let pb = BigInt::from(p);
        if (a.last().unwrap() % &pb).is_zero() || (b.last().unwrap() % &pb).is_zero() {
            continue;
        }
        let g = gcd_mod(reduce(&a, p), reduce(&b, p), p);
        let deg = g.len() - 1;
        if deg == 0 {
            return vec![BigInt::one()];
        }
        if best.is_some_and(|bd| deg > bd) {
            continue;
        }
        let gm = gamma.mod_floor(&pb).to_u64().unwrap();
        let scaled: Vec<u64> = g.iter().map(|&x| mulmod(gm, x, p)).collect();
        if best.is_none_or(|bd| deg < bd) {
            best = Some(deg);
            residues = scaled.iter().map(|&x| BigInt::from(x)).collect();
            modulus = pb.clone();
            previous = None;
        } else {
            let inv = powmod(modulus.mod_floor(&pb).to_u64().unwrap(), p - 2, p);
            for (r, &s) in residues.iter_mut().zip(&scaled) {
                let rm = r.mod_floor(&pb).to_u64().unwrap();
                let t = mulmod((s + p - rm) % p, inv, p);
                *r += &modulus * BigInt::from(t);
            }
            modulus *= &pb;
        }
        let half = &modulus / 2;
        let mut cand: Poly = residues
            .iter()
            .map(|r| if *r > half { r - &modulus } else { r.clone() })
            .collect();
        cand = primitive(cand);
        if cand.last().unwrap().is_negative() {
            cand = cand.into_iter().map(|x| -x).collect();
        }
        if previous.as_ref() == Some(&cand) && exact_div(&a, &cand).is_some() && exact_div(&b, &cand).is_some() {
            return cand;
        }
        previous = Some(cand);
    }
    unreachable!()
}

fn squarefree_part(a: &[BigInt]) -> (Poly, bool) {
    if a.len() <= 2 {
        return (a.to_vec(), true);
    }
    let da = derivative(a);
    for p in Primes((1 << 62) - 1000) {
        let pb = BigInt::from(p);
        if (a.last().unwrap() % &pb).is_zero() || (da.last().unwrap() % &pb).is_zero() {
            continue;
        }
        if gcd_mod(reduce(a, p), reduce(&da, p), p).len() == 1 {
            return (a.to_vec(), true);
        }
        break;
    }
    let g = int_gcd(a, &da);
    if g.len() == 1 {
        return (a.to_vec(), true);
    }
    (exact_div(a, &g).expect("gcd divides the polynomial"), false)
}

// ------------------------------------------------------------------------------------------
// Moebius transform and VCA bisection
// ------------------------------------------------------------------------------------------

fn mul_linear(a: &[BigInt], l: &(BigInt, BigInt)) -> Poly {
    let mut out = vec![BigInt::zero(); a.len() + 1];
    for (i, x) in a.iter().enumerate() {
        out[i] += &l.0 * x;
        out[i + 1] += &l.1 * x;
    }
    out
}

/// sum_i c_i A(y)^i B(y)^(m-i): S_m = c_m, S_k = S_{k+1} A + c_k B^(m-k), R = S_0.
fn transform(c: &[BigInt], a: &(BigInt, BigInt), b: &(BigInt, BigInt)) -> Poly {
    let m = c.len() - 1;
    let mut acc: Poly = vec![c[m].clone()];
    let mut bpow: Poly = vec![BigInt::one()];
    for k in (0..m).rev() {
        bpow = mul_linear(&bpow, b);
        acc = mul_linear(&acc, a);
        for (t, x) in acc.iter_mut().zip(&bpow) {
            *t += &c[k] * x;
        }
    }
    acc
}

fn taylor_shift_1(a: &mut [BigInt]) {
    let n = a.len();
    for i in 0..n.saturating_sub(1) {
        for jj in (i..n - 1).rev() {
            let (lo, hi) = a.split_at_mut(jj + 1);
            lo[jj] += &hi[0];
        }
    }
}

fn sign_variations(a: &[BigInt]) -> u32 {
    let mut count = 0;
    let mut last = 0i8;
    for x in a {
        let s = if x.is_positive() {
            1
        } else if x.is_negative() {
            -1
        } else {
            0
        };
        if s != 0 {
            if last != 0 && s != last {
                count += 1;
            }
            last = s;
        }
    }
    count
}

/// Sign variations of (t+1)^m q(1/(t+1)) (Descartes' bound for roots in (0, 1)), capped at 2.
fn descartes_01(q: &[BigInt]) -> u32 {
    if sign_variations(q) == 0 {
        return 0;
    }
    let mut a: Poly = q.iter().rev().cloned().collect();
    let n = a.len();
    let mut count = 0;
    let mut last = 0i8;
    for i in 0..n {
        if i + 1 < n {
            for jj in (i..n - 1).rev() {
                let (lo, hi) = a.split_at_mut(jj + 1);
                lo[jj] += &hi[0];
            }
        }
        let s = if a[i].is_positive() {
            1
        } else if a[i].is_negative() {
            -1
        } else {
            0
        };
        if s != 0 {
            if last != 0 && s != last {
                count += 1;
                if count >= 2 {
                    return 2;
                }
            }
            last = s;
        }
    }
    count
}

fn drop_twos(p: Poly) -> Poly {
    let shift = p.iter().filter_map(|x| x.trailing_zeros()).min().unwrap_or(0);
    if shift == 0 {
        p
    } else {
        p.into_iter().map(|x| x >> shift).collect()
    }
}

/// A dyadic point num / 2^k of [0, 1].
type Dyadic = (BigInt, u64);

struct Isolation {
    zero_leaves: Vec<Dyadic>, // [num/2^k, (num+1)/2^k] without roots inside
    one_leaves: Vec<Dyadic>,  // exactly one root inside, none at the endpoints
    dyadic_roots: Vec<Dyadic>,
    root_at_0: bool,
    root_at_1: bool,
}

fn isolate(r: &[BigInt]) -> Isolation {
    let m = r.len() - 1;
    let total: BigInt = r.iter().sum();
    let mut iso = Isolation {
        zero_leaves: vec![],
        one_leaves: vec![],
        dyadic_roots: vec![],
        root_at_0: r[0].is_zero(),
        root_at_1: total.is_zero(),
    };
    // breadth-first: the nodes of one level of the bisection tree are independent and are
    // processed in parallel; results are collected in order, so the outcome is deterministic
    let threads = std::thread::available_parallelism().map_or(1, |t| t.get()).clamp(1, 16);
    let mut level: Vec<(Poly, BigInt, u64)> = vec![(drop_twos(r.to_vec()), BigInt::zero(), 0)];
    while !level.is_empty() {
        let chunk = level.len().div_ceil(threads);
        let results: Vec<Node> = std::thread::scope(|scope| {
            let handles: Vec<_> = level
                .chunks(chunk)
                .map(|part| scope.spawn(move || part.iter().map(|(q, _, _)| visit(q, m)).collect::<Vec<Node>>()))
                .collect();
            handles
                .into_iter()
                .flat_map(|h| h.join().expect("worker thread"))
                .collect()
        });
        let mut next = Vec::new();
        for ((_, num, k), node) in level.into_iter().zip(results) {
            match node {
                Node::Zero => iso.zero_leaves.push((num, k)),
                Node::One => iso.one_leaves.push((num, k)),
                Node::Split {
                    left,
                    right,
                    midpoint_root,
                } => {
                    let n2: BigInt = &num * 2;
                    if midpoint_root {
                        iso.dyadic_roots.push((&n2 + 1, k + 1));
                    }
                    next.push((left, n2.clone(), k + 1));
                    next.push((right, n2 + 1, k + 1));
                }
            }
        }
        level = next;
    }
    iso
}

enum Node {
    Zero,
    One,
    Split {
        left: Poly,
        right: Poly,
        midpoint_root: bool,
    },
}

/// One bisection step for the polynomial q on its local interval [0, 1].
fn visit(q: &[BigInt], m: usize) -> Node {
    let v = descartes_01(q);
    if v == 0 {
        return Node::Zero;
    }
    let at_1: BigInt = q.iter().sum();
    if v == 1 && !q[0].is_zero() && !at_1.is_zero() {
        return Node::One;
    }
    let left: Poly = q.iter().enumerate().map(|(i, x)| x << (m - i)).collect(); // 2^m q(t/2)
    let mut right = left.clone();
    taylor_shift_1(&mut right); // 2^m q((t+1)/2)
    let midpoint_root = right[0].is_zero();
    Node::Split {
        left: drop_twos(left),
        right: drop_twos(right),
        midpoint_root,
    }
}

/// sign of r(num / 2^k).
fn sign_at(r: &[BigInt], num: &BigInt, k: u64) -> i8 {
    let m = r.len() - 1;
    let mut acc = BigInt::zero();
    for jj in (0..=m).rev() {
        acc = acc * num + (&r[jj] << (k as usize * (m - jj)));
    }
    if acc.is_positive() {
        1
    } else if acc.is_negative() {
        -1
    } else {
        0
    }
}

fn eval_rational(c: &[BigRational], x: &BigRational) -> BigRational {
    c.iter().rev().fold(BigRational::zero(), |acc, coef| acc * x + coef)
}

fn x_of(num: &BigInt, k: u64, a: &(BigInt, BigInt), b: &(BigInt, BigInt)) -> String {
    let y = BigRational::new(num.clone(), BigInt::one() << (k as usize));
    let top = BigRational::from_integer(a.0.clone()) + BigRational::from_integer(a.1.clone()) * &y;
    let bottom = BigRational::from_integer(b.0.clone()) + BigRational::from_integer(b.1.clone()) * &y;
    if bottom.is_zero() {
        return if top.is_positive() { "inf".into() } else { "-inf".into() };
    }
    rational_str(&(top / bottom))
}

fn endpoint(j: &J, what: &str, infinite: &str) -> Res<Option<BigRational>> {
    if let J::Str(s) = j {
        if s == infinite {
            return Ok(None);
        }
    }
    parse_rational(j, what).map(Some)
}

pub fn check_polynomial(j: &J) -> Res<Outcome> {
    let c = fields(
        j,
        "polynomial_positive_interval",
        &["coefficients", "interval"],
        &["strict"],
    )?;
    let raw = c.arr("coefficients")?;
    if raw.is_empty() || raw.len() > MAX_DEGREE + 1 {
        return malformed(format!("field 'coefficients' must have 1..{} entries", MAX_DEGREE + 1));
    }
    let coeffs: Vec<BigRational> = raw
        .iter()
        .enumerate()
        .map(|(i, x)| parse_rational(x, &format!("coefficients[{i}]")))
        .collect::<Res<_>>()?;
    let interval = as_arr(c.req("interval"), "field 'interval'", Some(2))?;
    let lo = endpoint(&interval[0], "interval[0]", "-inf")?;
    let hi = endpoint(&interval[1], "interval[1]", "inf")?;
    if let (Some(a), Some(b)) = (&lo, &hi) {
        if a > b {
            return malformed("interval[0] must not exceed interval[1]");
        }
    }
    let strict = c.bool_or("strict", true)?;
    let relation = if strict { ">" } else { ">=" };
    let shown = format!(
        "[{}, {}]",
        lo.as_ref().map_or("-inf".to_string(), rational_str),
        hi.as_ref().map_or("inf".to_string(), rational_str)
    );
    let den = coeffs.iter().fold(BigInt::one(), |acc, x| acc.lcm(x.denom()));
    let p_int = strip(coeffs.iter().map(|x| x.numer() * (&den / x.denom())).collect());
    let mut stats = Stats::new();
    stats.null("degree").null("squarefree").null("roots_in_interval");
    if p_int.is_empty() {
        return Ok(if strict {
            Outcome::new(false, "P is the zero polynomial", &stats)
        } else {
            Outcome::new(true, format!("P is the zero polynomial, so P >= 0 on {shown}"), &stats)
        });
    }
    let m = p_int.len() - 1;
    stats.set("degree", m as u64);
    let p_int = primitive(p_int);

    if let (Some(a), Some(b)) = (&lo, &hi) {
        if a == b {
            let value = eval_rational(&coeffs, a);
            stats
                .set("squarefree", squarefree_part(&p_int).1)
                .set("roots_in_interval", u64::from(value.is_zero()));
            let ok = if strict {
                value.is_positive()
            } else {
                !value.is_negative()
            };
            let sign = if value.is_zero() {
                "zero"
            } else if value.is_positive() {
                "positive"
            } else {
                "negative"
            };
            return Ok(Outcome::new(
                ok,
                format!(
                    "the interval is the single point {}, where P is {sign}",
                    rational_str(a)
                ),
                &stats,
            ));
        }
    }
    let (s, squarefree) = squarefree_part(&p_int);
    stats.set("squarefree", squarefree);

    let big = |x: &BigInt| x.clone();
    let mut pieces: Vec<((BigInt, BigInt), (BigInt, BigInt))> = Vec::new();
    match (&lo, &hi) {
        (Some(a), Some(b)) => {
            let d = a.denom().lcm(b.denom());
            let alpha = a.numer() * (&d / a.denom());
            let beta = b.numer() * (&d / b.denom());
            pieces.push(((alpha.clone(), beta - alpha), (d, BigInt::zero())));
        }
        (Some(a), None) => {
            let (alpha, d) = (big(a.numer()), big(a.denom()));
            pieces.push(((alpha.clone(), &d - alpha), (d.clone(), -d)));
        }
        (None, Some(b)) => {
            let (beta, d) = (big(b.numer()), big(b.denom()));
            pieces.push(((beta.clone(), -beta - &d), (d.clone(), -d)));
        }
        (None, None) => {
            let (zero, one) = (BigInt::zero(), BigInt::one());
            pieces.push(((zero.clone(), -one.clone()), (one.clone(), -one.clone())));
            pieces.push(((zero, one.clone()), (one.clone(), -one)));
        }
    }
    let mut roots: u64 = 0;
    let mut witness_root: Option<(bool, Dyadic, usize)> = None; // (is_point, where, piece)
    let mut negative_at: Option<(Dyadic, usize)> = None;
    for (pi, (a, b)) in pieces.iter().enumerate() {
        let rs = transform(&s, a, b);
        let rp = if squarefree {
            rs.clone()
        } else {
            transform(&p_int, a, b)
        };
        let iso = isolate(&rs);
        roots += (iso.one_leaves.len() + iso.dyadic_roots.len()) as u64
            + u64::from(iso.root_at_0)
            + u64::from(iso.root_at_1);
        if witness_root.is_none() {
            witness_root = if iso.root_at_0 {
                Some((true, (BigInt::zero(), 0), pi))
            } else if let Some(dr) = iso.dyadic_roots.first() {
                Some((true, dr.clone(), pi))
            } else if let Some(ol) = iso.one_leaves.first() {
                Some((false, ol.clone(), pi))
            } else if iso.root_at_1 {
                Some((true, (BigInt::one(), 0), pi))
            } else {
                None
            };
        }
        if negative_at.is_none() {
            let mut samples: Vec<Dyadic> = iso.zero_leaves.iter().map(|(num, k)| (num * 2 + 1, k + 1)).collect();
            for (num, k) in &iso.one_leaves {
                samples.push((num.clone(), *k));
                samples.push((num + 1, *k));
            }
            for (num, k) in samples {
                if sign_at(&rp, &num, k) < 0 {
                    negative_at = Some(((num, k), pi));
                    break;
                }
            }
        }
    }
    if pieces.len() == 2 && p_int[0].is_zero() {
        roots -= 1; // x = 0 is the shared endpoint of both halves
    }
    stats.set("roots_in_interval", roots);
    if let Some(((num, k), pi)) = negative_at {
        let (a, b) = &pieces[pi];
        return Ok(Outcome::new(
            false,
            format!(
                "P is negative at x = {}, so P {relation} 0 fails on {shown}",
                x_of(&num, k, a, b)
            ),
            &stats,
        ));
    }
    if strict && roots > 0 {
        let (is_point, (num, k), pi) = witness_root.unwrap();
        let (a, b) = &pieces[pi];
        let place = if is_point {
            format!("x = {}", x_of(&num, k, a, b))
        } else {
            format!(
                "the open interval between {} and {}",
                x_of(&num, k, a, b),
                x_of(&(&num + 1), k, a, b)
            )
        };
        return Ok(Outcome::new(
            false,
            format!("P has {roots} distinct real root(s) in {shown}, e.g. at {place}; P > 0 fails"),
            &stats,
        ));
    }
    if roots > 0 {
        return Ok(Outcome::new(
            true,
            format!("P >= 0 on {shown}: P touches zero at {roots} point(s) and is positive elsewhere"),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("P > 0 on {shown} (degree {m}, no real roots in the interval)"),
        &stats,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn poly(v: &[i64]) -> Poly {
        v.iter().map(|&x| BigInt::from(x)).collect()
    }

    #[test]
    fn squarefree_and_gcd() {
        // (x - 1)^2 (x + 2) = x^3 - 3x + 2
        let p = poly(&[2, -3, 0, 1]);
        let (s, sq) = squarefree_part(&p);
        assert!(!sq);
        assert_eq!(s, poly(&[-2, 1, 1])); // (x - 1)(x + 2)
        let (_, sq2) = squarefree_part(&poly(&[-2, 0, 1]));
        assert!(sq2);
    }

    #[test]
    fn isolation_counts() {
        // (2y - 1)(4y - 1)(4y - 3) has three roots in (0, 1), two of them dyadic
        let r = poly(&[-3, 22, -48, 32]);
        let iso = isolate(&r);
        assert_eq!(iso.one_leaves.len() + iso.dyadic_roots.len(), 3);
    }

    #[test]
    fn taylor() {
        let mut a = poly(&[0, 0, 1]);
        taylor_shift_1(&mut a);
        assert_eq!(a, poly(&[1, 2, 1]));
    }
}
