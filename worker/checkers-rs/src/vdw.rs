//! `vdw_coloring`: no monochromatic k-term arithmetic progression in an r-colouring of {1..n}.
//!
//! For every difference d and colour c the starts of monochromatic progressions form the bitset
//! B & (B >> d) & (B >> 2d) & ... & (B >> (k-1)d) restricted to the first n - (k-1)d positions.
//! Each shifted copy is ANDed into an accumulator word array in one branch-free pass.

use crate::common::{fields, malformed, Outcome, Res, Stats};
use crate::json::J;
use std::sync::atomic::{AtomicUsize, Ordering};

const MAX_N: i64 = 50_000_000;

/// acc[w] &= bits [64 w + shift, 64 w + shift + 64) of `b`, for all w < acc.len().
#[inline]
fn and_shifted(acc: &mut [u64], b: &[u64], shift: usize) {
    let (ws, off) = (shift / 64, (shift % 64) as u32);
    let len = acc.len();
    let lo = &b[ws..ws + len];
    if off == 0 {
        for i in 0..len {
            acc[i] &= lo[i];
        }
    } else {
        let hi = &b[ws + 1..ws + 1 + len];
        let back = 64 - off;
        for i in 0..len {
            acc[i] &= (lo[i] >> off) | (hi[i] << back);
        }
    }
}

pub fn check_vdw(j: &J) -> Res<Outcome> {
    let c = fields(j, "vdw_coloring", &["n", "colours", "k", "colouring"], &[])?;
    let n = c.int("n", Some(1), Some(MAX_N))? as usize;
    let r = c.int("colours", Some(1), Some(10))? as usize;
    let k = c.int("k", Some(1), Some(MAX_N))? as usize;
    let text = c.str("colouring")?;
    if text.chars().count() != n {
        return malformed(format!("field 'colouring' must have length n = {n}"));
    }
    let bytes = text.as_bytes();
    if bytes.iter().any(|&b| b < b'0' || b >= b'0' + r as u8) {
        return malformed(format!("field 'colouring' must consist of digits 0..{}", r - 1));
    }
    let mut counts = vec![0u64; r];
    for &b in bytes {
        counts[(b - b'0') as usize] += 1;
    }
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("colours", r as u64)
        .set("k", k as u64)
        .set("colour_counts", counts);
    if k == 1 {
        return Ok(Outcome::new(
            false,
            "the single integer 1 is a monochromatic 1-term progression",
            &stats,
        ));
    }
    let words = n / 64 + 2;
    let mut sets = vec![vec![0u64; words]; r];
    for (i, &b) in bytes.iter().enumerate() {
        sets[(b - b'0') as usize][i / 64] |= 1u64 << (i % 64);
    }
    let max_d = (n - 1) / (k - 1);
    // differences are distributed over threads; each thread scans its differences in increasing
    // order and stops at its first violation, so the smallest violating d is found exactly
    let best = AtomicUsize::new(usize::MAX);
    let threads = std::thread::available_parallelism()
        .map_or(1, |t| t.get())
        .clamp(1, 16)
        .min(max_d.max(1));
    let found: Vec<Option<(usize, usize, usize)>> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..threads)
            .map(|tid| {
                let (sets, best) = (&sets, &best);
                scope.spawn(move || {
                    let mut acc: Vec<u64> = Vec::with_capacity(words);
                    for d in (1 + tid..=max_d).step_by(threads) {
                        if d > best.load(Ordering::Relaxed) {
                            return None;
                        }
                        let starts = n - (k - 1) * d; // positions 0..starts-1 <-> integers 1..starts
                        let nwords = starts.div_ceil(64);
                        for (colour, set) in sets.iter().enumerate() {
                            acc.clear();
                            acc.extend_from_slice(&set[..nwords]);
                            if !starts.is_multiple_of(64) {
                                acc[nwords - 1] &= (1u64 << (starts % 64)) - 1;
                            }
                            for t in 1..k {
                                and_shifted(&mut acc, set, t * d);
                                if t % 4 == 0 && acc.iter().all(|&x| x == 0) {
                                    break;
                                }
                            }
                            if let Some(w) = acc.iter().position(|&x| x != 0) {
                                best.fetch_min(d, Ordering::Relaxed);
                                return Some((d, colour, w * 64 + acc[w].trailing_zeros() as usize + 1));
                            }
                        }
                    }
                    None
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().expect("worker thread")).collect()
    });
    if let Some((d, colour, a)) = found.into_iter().flatten().min() {
        let terms: Vec<usize> = (0..k.min(12)).map(|t| a + t * d).collect();
        return Ok(Outcome::new(
            false,
            format!(
                "monochromatic {k}-term progression of colour {colour}: start {a}, difference {d}: {:?}{}",
                terms,
                if k > 12 { " ..." } else { "" }
            ),
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("no monochromatic {k}-term arithmetic progression in {{1..{n}}}; shows W({r},{k}) > {n}"),
        &stats,
    ))
}
