//! `affine_line_free_set`, `kissing_configuration`, `heilbronn_configuration`.

use crate::common::{as_arr, fields, int_list, malformed, parse_rational, rational_str, Outcome, Res, Stats};
use crate::json::J;
use num_bigint::BigInt;
use num_integer::Integer;
use num_rational::BigRational;
use num_traits::{One, Signed, Zero};
use std::collections::HashSet;

// ------------------------------------------------------------------------------------------
// affine_line_free_set
// ------------------------------------------------------------------------------------------

const MAX_POINTS: usize = 1 << 20;
const MAX_LINE_WORK: u128 = 1 << 36;

fn is_prime(p: i64) -> bool {
    p >= 2 && (2..).take_while(|i| i * i <= p).all(|i| p % i != 0)
}

pub fn check_affine_line_free(j: &J) -> Res<Outcome> {
    let c = fields(j, "affine_line_free_set", &["p", "dimension", "points"], &["size"])?;
    let p = c.int("p", Some(2), Some(1 << 20))?;
    if !is_prime(p) {
        return malformed(format!("field 'p' = {p} is not prime"));
    }
    let d = c.int("dimension", Some(1), Some(64))? as usize;
    let mut space: u128 = 1;
    for _ in 0..d {
        space = space.saturating_mul(p as u128);
    }
    if space >= 1 << 62 {
        return malformed("p^dimension must be below 2^62");
    }
    let pts_j = c.arr("points")?;
    if pts_j.len() > MAX_POINTS {
        return malformed(format!("more than {MAX_POINTS} points"));
    }
    let mut pts: Vec<Vec<u64>> = Vec::with_capacity(pts_j.len());
    for (i, pt) in pts_j.iter().enumerate() {
        let v = int_list(pt, &format!("points[{i}]"), Some(d), Some(0), Some(p - 1))?;
        pts.push(v.iter().map(|&x| x as u64).collect());
    }
    let claimed = c.opt_int("size", Some(0), None)?;
    let count = pts.len();
    let p = p as u64;
    let directions = (space - 1) / (p as u128 - 1);
    if directions.min(count as u128) * count as u128 > MAX_LINE_WORK {
        return malformed("instance too large: min(#directions, |S|) * |S| exceeds 2^36");
    }
    let encode = |v: &[u64]| -> u64 { v.iter().rev().fold(0u64, |acc, &x| acc * p + x) };
    let mut stats = Stats::new();
    stats
        .set("p", p)
        .set("dimension", d as u64)
        .set("size", count as u64)
        .null("full_lines");
    let mut members: HashSet<u64> = HashSet::with_capacity(count);
    let mut first_index = std::collections::HashMap::new();
    for (i, v) in pts.iter().enumerate() {
        let code = encode(v);
        if !members.insert(code) {
            let first = first_index[&code];
            return Ok(Outcome::new(
                false,
                format!("points[{first}] and points[{i}] are equal"),
                &stats,
            ));
        }
        first_index.insert(code, i);
    }
    if let Some(cs) = claimed {
        if cs as usize != count {
            return Ok(Outcome::new(
                false,
                format!("claimed size {cs} but {count} points are given"),
                &stats,
            ));
        }
    }
    let point_on = |a: &[u64], v: &[u64], t: u64| -> u64 {
        let w: Vec<u64> = a.iter().zip(v).map(|(&x, &y)| (x + t * y) % p).collect();
        encode(&w)
    };
    let mut full: u128 = 0;
    let mut witness: Option<(Vec<u64>, Vec<u64>)> = None;
    if count >= 2 && (count as u128) <= 2 * directions {
        // pairs {a, b}: is the line a + t(b - a) inside S?  each full line is seen C(p, 2) times
        for i in 0..count {
            for jj in i + 1..count {
                let v: Vec<u64> = pts[i].iter().zip(&pts[jj]).map(|(&x, &y)| (y + p - x) % p).collect();
                if (2..p).all(|t| members.contains(&point_on(&pts[i], &v, t))) {
                    full += 1;
                    witness.get_or_insert((pts[i].clone(), v));
                }
            }
        }
        full /= u128::from(p) * u128::from(p - 1) / 2;
    } else if count >= 2 {
        // normalised directions (first nonzero coordinate 1) x base points; each full line p times
        let mut v = vec![0u64; d];
        for lead in 0..d {
            let free = d - lead - 1;
            let total = p.pow(free as u32);
            for idx in 0..total {
                v.iter_mut().for_each(|x| *x = 0);
                v[lead] = 1;
                let mut rest = idx;
                for slot in v.iter_mut().skip(lead + 1) {
                    *slot = rest % p;
                    rest /= p;
                }
                for a in &pts {
                    if (1..p).all(|t| members.contains(&point_on(a, &v, t))) {
                        full += 1;
                        witness.get_or_insert((a.clone(), v.clone()));
                    }
                }
            }
        }
        full /= u128::from(p);
    }
    stats.set("full_lines", full as u64);
    if full > 0 {
        let (a, v) = witness.unwrap();
        return Ok(Outcome::new(
            false,
            format!("S contains {full} full affine line(s), e.g. {{{a:?} + t*{v:?}}}"),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("no affine line of F_{p}^{d} lies in S; |S| = {count}"),
        &stats,
    ))
}

// ------------------------------------------------------------------------------------------
// kissing_configuration
// ------------------------------------------------------------------------------------------

const MAX_VECTORS: usize = 100_000;
const MAX_SQUARED_NORM: i128 = 1 << 61;

pub fn check_kissing(j: &J) -> Res<Outcome> {
    let c = fields(j, "kissing_configuration", &["dimension", "vectors"], &["squared_norm"])?;
    let dim = c.int("dimension", Some(1), Some(1024))? as usize;
    let vecs_j = c.arr("vectors")?;
    if vecs_j.is_empty() {
        return malformed("field 'vectors' must not be empty");
    }
    if vecs_j.len() > MAX_VECTORS {
        return malformed(format!("more than {MAX_VECTORS} vectors"));
    }
    let count = vecs_j.len();
    let mut x = vec![0i64; count * dim];
    let mut norms = vec![0i64; count];
    for (i, v) in vecs_j.iter().enumerate() {
        let J::Arr(items) = v else {
            return malformed(format!("vectors[{i}] must be an array of {dim} integers"));
        };
        if items.len() != dim {
            return malformed(format!("vectors[{i}] must be an array of {dim} integers"));
        }
        let mut total: i128 = 0;
        let mut too_big = false;
        for (t, item) in items.iter().enumerate() {
            let J::Int(val) = item else {
                return malformed(format!("vectors[{i}] must contain integers only"));
            };
            if val.unsigned_abs() > 1 << 31 {
                too_big = true;
            } else {
                total += i128::from(*val) * i128::from(*val);
            }
            x[i * dim + t] = *val;
        }
        if too_big || total > MAX_SQUARED_NORM {
            return malformed(format!("vectors[{i}] has squared norm above 2^61"));
        }
        norms[i] = total as i64;
    }
    let claimed = c.opt_int("squared_norm", Some(1), Some(1 << 61))?;
    let mut stats = Stats::new();
    stats
        .set("dimension", dim as u64)
        .set("count", count as u64)
        .null("touching_pairs")
        .null("violating_pairs");
    if let Some(i) = norms.iter().position(|&t| t == 0) {
        return Ok(Outcome::new(false, format!("vectors[{i}] is the zero vector"), &stats));
    }
    if let Some(cn) = claimed {
        if let Some(i) = norms.iter().position(|&t| t != cn) {
            return Ok(Outcome::new(
                false,
                format!("vectors[{i}] has squared norm {}, claimed {cn}", norms[i]),
                &stats,
            ));
        }
    }
    // all pairs i < j; |<x_i, x_j>| <= sqrt(N_i N_j) <= 2^61 (also for every partial sum), so
    // i64 dot products are exact and (2d)^2, N_i N_j <= 2^124 fit in i128.
    let threads = std::thread::available_parallelism().map_or(1, |t| t.get()).min(16);
    let rows_of = |tid: usize| (tid..count).step_by(threads);
    let results: Vec<(u64, u64, Option<(usize, usize)>)> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..threads)
            .map(|tid| {
                let (x, norms) = (&x, &norms);
                scope.spawn(move || {
                    let mut touching = 0u64;
                    let mut violating = 0u64;
                    let mut first: Option<(usize, usize)> = None;
                    for i in rows_of(tid) {
                        let xi = &x[i * dim..(i + 1) * dim];
                        for jj in i + 1..count {
                            let xj = &x[jj * dim..(jj + 1) * dim];
                            let dot: i64 = xi.iter().zip(xj).map(|(a, b)| a * b).sum();
                            if dot <= 0 {
                                continue;
                            }
                            let lhs = 4 * i128::from(dot) * i128::from(dot);
                            let rhs = i128::from(norms[i]) * i128::from(norms[jj]);
                            if lhs == rhs {
                                touching += 1;
                            } else if lhs > rhs {
                                violating += 1;
                                if first.is_none_or(|f| (i, jj) < f) {
                                    first = Some((i, jj));
                                }
                            }
                        }
                    }
                    (touching, violating, first)
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().expect("worker thread")).collect()
    });
    let touching: u64 = results.iter().map(|r| r.0).sum();
    let violating: u64 = results.iter().map(|r| r.1).sum();
    let first = results.iter().filter_map(|r| r.2).min();
    stats.set("touching_pairs", touching).set("violating_pairs", violating);
    if let Some((i, jj)) = first {
        let dot: i64 = (0..dim).map(|t| x[i * dim + t] * x[jj * dim + t]).sum();
        let what = if x[i * dim..(i + 1) * dim] == x[jj * dim..(jj + 1) * dim] {
            "are equal"
        } else {
            "make an angle below 60 degrees"
        };
        return Ok(Outcome::new(
            false,
            format!(
                "{violating} pair(s) violate the 60-degree condition, e.g. vectors[{i}] and vectors[{jj}] {what} (inner product {dot}, squared norms {} and {})",
                norms[i], norms[jj]
            ),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("{count} nonzero vectors in Z^{dim} with pairwise angles >= 60 degrees ({touching} pairs at exactly 60 degrees): a kissing configuration of size {count}"),
        &stats,
    ))
}

// ------------------------------------------------------------------------------------------
// heilbronn_configuration
// ------------------------------------------------------------------------------------------

const MAX_HEILBRONN_POINTS: usize = 500;

type Pt = (BigRational, BigRational);

fn point(j: &J, what: &str) -> Res<Pt> {
    let items = match as_arr(j, what, Some(2)) {
        Ok(items) => items,
        Err(_) => return malformed(format!("{what} must be a pair [x, y]")),
    };
    Ok((
        parse_rational(&items[0], &format!("{what}[0]"))?,
        parse_rational(&items[1], &format!("{what}[1]"))?,
    ))
}

fn cross(o: &Pt, a: &Pt, b: &Pt) -> BigRational {
    (&a.0 - &o.0) * (&b.1 - &o.1) - (&a.1 - &o.1) * (&b.0 - &o.0)
}

pub fn check_heilbronn(j: &J) -> Res<Outcome> {
    let c = fields(j, "heilbronn_configuration", &["region", "points", "min_area"], &[])?;
    let zero = || BigRational::zero();
    let one = || BigRational::one();
    let (square, tri, region_area): (bool, Vec<Pt>, BigRational) = match c.req("region") {
        J::Str(s) if s == "unit_square" => (true, vec![], one()),
        J::Str(s) if s == "unit_triangle" => (
            false,
            vec![(zero(), zero()), (one(), zero()), (zero(), one())],
            BigRational::new(BigInt::one(), BigInt::from(2)),
        ),
        J::Obj(items) => {
            if items.len() != 1 || items[0].0 != "triangle" {
                return malformed("an explicit region must be {\"triangle\": [[x,y],[x,y],[x,y]]}");
            }
            let verts = match &items[0].1 {
                J::Arr(v) if v.len() == 3 => v,
                _ => return malformed("region.triangle must list exactly 3 vertices"),
            };
            let tri: Vec<Pt> = verts
                .iter()
                .enumerate()
                .map(|(i, v)| point(v, &format!("region.triangle[{i}]")))
                .collect::<Res<_>>()?;
            let area = cross(&tri[0], &tri[1], &tri[2]).abs() / BigRational::from_integer(BigInt::from(2));
            if area.is_zero() {
                return malformed("region.triangle is degenerate (zero area)");
            }
            (false, tri, area)
        }
        _ => return malformed("field 'region' must be 'unit_square', 'unit_triangle' or a triangle object"),
    };
    let raw = c.arr("points")?;
    if raw.len() < 3 || raw.len() > MAX_HEILBRONN_POINTS {
        return malformed(format!("between 3 and {MAX_HEILBRONN_POINTS} points are required"));
    }
    let pts: Vec<Pt> = raw
        .iter()
        .enumerate()
        .map(|(i, v)| point(v, &format!("points[{i}]")))
        .collect::<Res<_>>()?;
    let claimed = parse_rational(c.req("min_area"), "field 'min_area'")?;
    if claimed.is_negative() {
        return malformed("field 'min_area' must be nonnegative");
    }
    let n = pts.len();
    let mut stats = Stats::new();
    stats
        .set("points", n as u64)
        .null("min_area")
        .null("normalized_min_area");
    for (i, pt) in pts.iter().enumerate() {
        let inside = if square {
            !pt.0.is_negative() && !pt.1.is_negative() && pt.0 <= one() && pt.1 <= one()
        } else {
            let s: Vec<BigRational> = (0..3).map(|e| cross(&tri[e], &tri[(e + 1) % 3], pt)).collect();
            s.iter().all(|v| !v.is_negative()) || s.iter().all(|v| !v.is_positive())
        };
        if !inside {
            return Ok(Outcome::new(
                false,
                format!(
                    "points[{i}] = ({}, {}) lies outside the region",
                    rational_str(&pt.0),
                    rational_str(&pt.1)
                ),
                &stats,
            ));
        }
    }
    // integers on the common denominator D: twice the area of a triangle is |cross| / D^2
    let mut den = BigInt::one();
    for (x, y) in &pts {
        den = den.lcm(x.denom()).lcm(y.denom());
    }
    let xs: Vec<BigInt> = pts.iter().map(|(x, _)| x.numer() * (&den / x.denom())).collect();
    let ys: Vec<BigInt> = pts.iter().map(|(_, y)| y.numer() * (&den / y.denom())).collect();
    let mut best: Option<(BigInt, (usize, usize, usize))> = None;
    for i in 0..n {
        for jj in i + 1..n {
            let dx = &xs[jj] - &xs[i];
            let dy = &ys[jj] - &ys[i];
            for k in jj + 1..n {
                let c2 = (&dx * (&ys[k] - &ys[i]) - &dy * (&xs[k] - &xs[i])).abs();
                if best.as_ref().is_none_or(|(b, _)| c2 < *b) {
                    best = Some((c2, (i, jj, k)));
                }
            }
        }
    }
    let (best, (i, jj, k)) = best.unwrap();
    let min_area = BigRational::new(best, BigInt::from(2) * &den * &den);
    stats
        .set("min_area", rational_str(&min_area))
        .set("normalized_min_area", rational_str(&(&min_area / &region_area)));
    if min_area < claimed {
        return Ok(Outcome::new(
            false,
            format!(
                "the triangle on points {i}, {jj}, {k} has area {} < claimed {}",
                rational_str(&min_area),
                rational_str(&claimed)
            ),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!(
            "all {n} points lie in the region and every triangle has area >= {}; exact minimum area {} (triangle {i}, {jj}, {k})",
            rational_str(&claimed),
            rational_str(&min_area)
        ),
        &stats,
    ))
}
