//! `linear_code` and `covering_code`.

use crate::common::{as_arr, fields, int_list, malformed, Outcome, Res, Stats};
use crate::json::J;
use serde_json::Value;

const MAX_Q: i64 = 1024;
const MAX_N: i64 = 100_000;
const MAX_ENUMERATION: u128 = 1 << 34;
const MAX_COVER_N: i64 = 28;

// ------------------------------------------------------------------------------------------
// GF(p^m): the integer sum c_i p^i encodes sum c_i x^i; multiplication by schoolbook
// polynomial product and reduction modulo the monic modulus.
// ------------------------------------------------------------------------------------------

struct Field {
    q: usize,
    add: Vec<u16>,
    mul: Vec<u16>,
    neg: Vec<u16>,
    inv: Vec<u16>,
}

impl Field {
    fn new(p: usize, m: usize, modulus: &[usize]) -> Res<Field> {
        let q = p.pow(m as u32);
        let digits = |mut a: usize| -> Vec<usize> {
            let mut d = vec![0; m];
            for slot in d.iter_mut() {
                *slot = a % p;
                a /= p;
            }
            d
        };
        let encode = |d: &[usize]| -> usize { d.iter().rev().fold(0, |acc, &x| acc * p + x) };
        let all: Vec<Vec<usize>> = (0..q).map(digits).collect();
        let mut add = vec![0u16; q * q];
        let mut mul = vec![0u16; q * q];
        for a in 0..q {
            for b in 0..q {
                let s: Vec<usize> = (0..m).map(|i| (all[a][i] + all[b][i]) % p).collect();
                add[a * q + b] = encode(&s) as u16;
                let mut prod = vec![0usize; 2 * m - 1];
                for i in 0..m {
                    for jj in 0..m {
                        prod[i + jj] = (prod[i + jj] + all[a][i] * all[b][jj]) % p;
                    }
                }
                for top in (m..2 * m - 1).rev() {
                    let c = prod[top];
                    if c != 0 {
                        for i in 0..=m {
                            let idx = top - m + i;
                            prod[idx] = (prod[idx] + (p - c) * modulus[i]) % p;
                        }
                    }
                }
                mul[a * q + b] = encode(&prod[..m]) as u16;
            }
        }
        // the quotient ring is a field iff it has no zero divisors
        for a in 1..q {
            if (1..q).any(|b| mul[a * q + b] == 0) {
                return malformed("field.modulus is not irreducible over GF(p)");
            }
        }
        let neg: Vec<u16> = (0..q)
            .map(|a| (0..q).find(|&b| add[a * q + b] == 0).unwrap() as u16)
            .collect();
        let inv: Vec<u16> = (0..q)
            .map(|a| {
                if a == 0 {
                    0
                } else {
                    (1..q).find(|&b| mul[a * q + b] == 1).unwrap() as u16
                }
            })
            .collect();
        Ok(Field { q, add, mul, neg, inv })
    }
    #[inline]
    fn add(&self, a: u16, b: u16) -> u16 {
        self.add[a as usize * self.q + b as usize]
    }
    #[inline]
    fn mul(&self, a: u16, b: u16) -> u16 {
        self.mul[a as usize * self.q + b as usize]
    }
}

fn is_prime(p: i64) -> bool {
    p >= 2 && (2..).take_while(|i| i * i <= p).all(|i| p % i != 0)
}

fn parse_field(spec: &J) -> Res<Field> {
    let items = match spec {
        J::Obj(items) => items,
        _ => return malformed("field 'field' must be an object"),
    };
    for (key, _) in items {
        if !["p", "m", "modulus"].contains(&key.as_str()) {
            return malformed(format!("unknown key {key:?} in 'field'"));
        }
    }
    let p = match spec.get("p") {
        Some(v) => crate::common::as_int(v, "field.p", Some(2), Some(MAX_Q))?,
        None => return malformed("missing key 'p' in 'field'"),
    };
    if !is_prime(p) {
        return malformed(format!("field.p = {p} is not prime"));
    }
    let m = match spec.get("m") {
        Some(v) => crate::common::as_int(v, "field.m", Some(1), Some(10))?,
        None => 1,
    };
    let q = (p as u128).pow(m as u32);
    if q > MAX_Q as u128 {
        return malformed(format!("q = p^m = {q} exceeds {MAX_Q}"));
    }
    let (p, m) = (p as usize, m as usize);
    if m == 1 {
        if spec.get("modulus").is_some() {
            return malformed("field.modulus must be omitted for a prime field (m = 1)");
        }
        return Field::new(p, 1, &[0, 1]);
    }
    let Some(modulus) = spec.get("modulus") else {
        return malformed("field.modulus is required when m > 1");
    };
    let modulus = int_list(modulus, "field.modulus", Some(m + 1), Some(0), Some(p as i64 - 1))?;
    if modulus[m] != 1 {
        return malformed("field.modulus must be monic (last coefficient 1)");
    }
    let modulus: Vec<usize> = modulus.iter().map(|&x| x as usize).collect();
    Field::new(p, m, &modulus)
}

/// Gaussian elimination over GF(q); returns a basis of the row space.
fn row_basis(f: &Field, mut rows: Vec<Vec<u16>>, n: usize) -> Vec<Vec<u16>> {
    let mut rank = 0;
    for col in 0..n {
        let Some(piv) = (rank..rows.len()).find(|&i| rows[i][col] != 0) else {
            continue;
        };
        rows.swap(rank, piv);
        let inv = f.inv[rows[rank][col] as usize];
        let pivot_row: Vec<u16> = rows[rank].iter().map(|&x| f.mul(inv, x)).collect();
        rows[rank] = pivot_row.clone();
        for i in 0..rows.len() {
            if i != rank && rows[i][col] != 0 {
                let factor = f.neg[rows[i][col] as usize];
                for (x, &y) in rows[i].iter_mut().zip(&pivot_row) {
                    *x = f.add(*x, f.mul(factor, y));
                }
            }
        }
        rank += 1;
        if rank == rows.len() {
            break;
        }
    }
    rows.truncate(rank);
    rows
}

/// Weight distribution of the row space of `basis` by the modular q-ary Gray code on the
/// coefficient encodings: consecutive coefficient vectors differ in one coordinate t whose
/// encoding e becomes (e + 1) mod q, so the codeword changes by (new - old) * basis[t].
fn weight_distribution(f: &Field, basis: &[Vec<u16>], n: usize) -> Vec<u64> {
    let r = basis.len();
    let q = f.q;
    let mut hist = vec![0u64; n + 1];
    let mut word = vec![0u16; n];
    let mut weight = 0usize;
    let mut digits = vec![0usize; r];
    let mut coef = vec![0usize; r];
    hist[0] = 1;
    if r == 0 {
        return hist;
    }
    loop {
        // increment the base-q counter; t = position of the digit that increases
        let mut t = 0;
        while t < r && digits[t] == q - 1 {
            digits[t] = 0;
            t += 1;
        }
        if t == r {
            break;
        }
        digits[t] += 1;
        let old = coef[t];
        let new = (old + 1) % q;
        coef[t] = new;
        let delta = f.add(new as u16, f.neg[old]);
        for (x, &g) in word.iter_mut().zip(&basis[t]) {
            if g != 0 {
                let before = *x != 0;
                *x = f.add(*x, f.mul(delta, g));
                let after = *x != 0;
                if before != after {
                    if after {
                        weight += 1;
                    } else {
                        weight -= 1;
                    }
                }
            }
        }
        hist[weight] += 1;
    }
    hist
}

pub fn check_linear_code(j: &J) -> Res<Outcome> {
    let c = fields(
        j,
        "linear_code",
        &["field", "n", "k", "generator_matrix", "minimum_distance"],
        &[],
    )?;
    let f = parse_field(c.req("field"))?;
    let n = c.int("n", Some(1), Some(MAX_N))? as usize;
    let k = c.int("k", Some(1), Some(n as i64))? as usize;
    let d = c.int("minimum_distance", Some(1), Some(n as i64))? as usize;
    let rows_j = as_arr(c.req("generator_matrix"), "field 'generator_matrix'", Some(k))?;
    let mut rows = Vec::with_capacity(k);
    for (i, row) in rows_j.iter().enumerate() {
        let v = int_list(
            row,
            &format!("generator_matrix[{i}]"),
            Some(n),
            Some(0),
            Some(f.q as i64 - 1),
        )?;
        rows.push(v.iter().map(|&x| x as u16).collect::<Vec<u16>>());
    }
    let work = (f.q as u128)
        .checked_pow(k as u32)
        .and_then(|x| x.checked_mul(n as u128));
    if work.is_none_or(|w| w > MAX_ENUMERATION) {
        return malformed("q^k * n exceeds the enumeration limit 2^34");
    }
    let basis = row_basis(&f, rows, n);
    let rank = basis.len();
    let hist = weight_distribution(&f, &basis, n);
    let dist: Vec<Value> = (1..=n)
        .filter(|&w| hist[w] > 0)
        .map(|w| Value::from(vec![w as u64, hist[w]]))
        .collect();
    let true_d = (1..=n).find(|&w| hist[w] > 0);
    let mut stats = Stats::new();
    stats
        .set("q", f.q as u64)
        .set("n", n as u64)
        .set("k", k as u64)
        .set("rank", rank as u64);
    match true_d {
        Some(t) => stats.set("minimum_distance", t as u64),
        None => stats.null("minimum_distance"),
    };
    stats.set("weight_distribution", Value::Array(dist));
    if rank < k {
        return Ok(Outcome::new(
            false,
            format!("the generator matrix has rank {rank} < k = {k} over GF({})", f.q),
            &stats,
        ));
    }
    if true_d != Some(d) {
        return Ok(Outcome::new(
            false,
            format!(
                "claimed minimum distance {d}, but the true minimum distance is {}",
                true_d.map_or("undefined".into(), |t| t.to_string())
            ),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("[{n},{k},{d}] linear code over GF({}) verified", f.q),
        &stats,
    ))
}

// ------------------------------------------------------------------------------------------
// covering_code: bitset of F_2^n, `radius` rounds of dilation (flip one coordinate).
// ------------------------------------------------------------------------------------------

const FLIP_MASKS: [u64; 6] = [
    0x5555_5555_5555_5555,
    0x3333_3333_3333_3333,
    0x0F0F_0F0F_0F0F_0F0F,
    0x00FF_00FF_00FF_00FF,
    0x0000_FFFF_0000_FFFF,
    0x0000_0000_FFFF_FFFF,
];

fn dilate(cov: &[u64], n: usize) -> Vec<u64> {
    let mut out = cov.to_vec();
    for i in 0..n {
        if i < 6 {
            let s = 1u32 << i;
            let m = FLIP_MASKS[i];
            for (o, &w) in out.iter_mut().zip(cov) {
                *o |= ((w & m) << s) | ((w >> s) & m);
            }
        } else {
            let stride = 1usize << (i - 6);
            for (idx, o) in out.iter_mut().enumerate() {
                *o |= cov[idx ^ stride];
            }
        }
    }
    out
}

pub fn check_covering_code(j: &J) -> Res<Outcome> {
    let c = fields(j, "covering_code", &["n", "radius", "codewords"], &["size"])?;
    let n = c.int("n", Some(1), Some(MAX_COVER_N))? as usize;
    let radius = c.int("radius", Some(0), Some(n as i64))? as usize;
    let words_j = c.arr("codewords")?;
    if words_j.is_empty() {
        return malformed("field 'codewords' must not be empty");
    }
    let mut values = Vec::with_capacity(words_j.len());
    for (i, w) in words_j.iter().enumerate() {
        let J::Str(s) = w else {
            return malformed(format!("codewords[{i}] must be a string"));
        };
        if s.len() != n || !s.bytes().all(|b| b == b'0' || b == b'1') {
            return malformed(format!("codewords[{i}] must be a string of {n} characters 0/1"));
        }
        let v = s
            .bytes()
            .enumerate()
            .fold(0u64, |acc, (pos, b)| acc | (u64::from(b - b'0') << pos));
        values.push(v);
    }
    let claimed = c.opt_int("size", Some(1), None)?;
    let mut sorted: Vec<(u64, usize)> = values.iter().copied().zip(0..).collect();
    sorted.sort();
    let distinct = 1 + sorted.windows(2).filter(|w| w[0].0 != w[1].0).count();
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("radius", radius as u64)
        .set("size", distinct as u64)
        .null("uncovered");
    if distinct != values.len() {
        // report the earliest repeated codeword
        let mut first_dup: Option<(usize, usize)> = None;
        for w in sorted.windows(2) {
            if w[0].0 == w[1].0 {
                let pair = (w[0].1, w[1].1);
                if first_dup.is_none_or(|f| pair.1 < f.1) {
                    first_dup = Some(pair);
                }
            }
        }
        let (a, b) = first_dup.unwrap();
        return Ok(Outcome::new(
            false,
            format!("codewords[{a}] and codewords[{b}] are equal"),
            &stats,
        ));
    }
    if let Some(cs) = claimed {
        if cs as usize != distinct {
            return Ok(Outcome::new(
                false,
                format!("claimed size {cs} but the code has {distinct} codewords"),
                &stats,
            ));
        }
    }
    let nbits = 1usize << n;
    let words = nbits.div_ceil(64);
    let mut cov = vec![0u64; words];
    for &v in &values {
        cov[(v / 64) as usize] |= 1u64 << (v % 64);
    }
    for _ in 0..radius {
        cov = dilate(&cov, n);
    }
    let covered: u64 = cov.iter().map(|w| u64::from(w.count_ones())).sum();
    let uncovered = nbits as u64 - covered;
    stats.set("uncovered", uncovered);
    if uncovered > 0 {
        let x = (0..nbits).find(|&x| cov[x / 64] >> (x % 64) & 1 == 0).unwrap();
        let word: String = (0..n).map(|i| if x >> i & 1 == 1 { '1' } else { '0' }).collect();
        return Ok(Outcome::new(
            false,
            format!("{uncovered} words are at distance > {radius} from the code, e.g. {word}"),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!(
            "every word of F_2^{n} is within distance {radius} of one of the {distinct} codewords; shows K({n},{radius}) <= {distinct}"
        ),
        &stats,
    ))
}
