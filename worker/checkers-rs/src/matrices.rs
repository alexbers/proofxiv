//! `hadamard_matrix` and `circulant_weighing_matrix`.

use crate::common::{as_arr, fields, int_list, malformed, Outcome, Res, Stats};
use crate::json::J;

const MAX_ORDER: i64 = 4096;
const MAX_CW_ORDER: i64 = 100_000;

pub fn check_hadamard(j: &J) -> Res<Outcome> {
    let c = fields(j, "hadamard_matrix", &["n", "rows"], &["symmetric"])?;
    let n = c.int("n", Some(1), Some(MAX_ORDER))? as usize;
    let want_symmetric = c.bool_or("symmetric", false)?;
    let rows_j = as_arr(c.req("rows"), "field 'rows'", Some(n))?;
    let mut h = vec![0i8; n * n];
    for (i, row) in rows_j.iter().enumerate() {
        let J::Str(s) = row else {
            return malformed(format!("rows[{i}] must be a string"));
        };
        if s.len() != n || !s.bytes().all(|b| b == b'+' || b == b'-') {
            return malformed(format!("rows[{i}] must be a string of {n} characters '+'/'-'"));
        }
        for (jj, b) in s.bytes().enumerate() {
            h[i * n + jj] = if b == b'+' { 1 } else { -1 };
        }
    }
    let asym = (0..n)
        .flat_map(|i| (i + 1..n).map(move |jj| (i, jj)))
        .find(|&(i, jj)| h[i * n + jj] != h[jj * n + i]);
    let mut stats = Stats::new();
    stats.set("n", n as u64).set("symmetric", asym.is_none());
    // all inner products of rows i <= j (exact: |value| <= n)
    for i in 0..n {
        let ri = &h[i * n..(i + 1) * n];
        for jj in i..n {
            let rj = &h[jj * n..(jj + 1) * n];
            let dot: i32 = ri.iter().zip(rj).map(|(&a, &b)| i32::from(a) * i32::from(b)).sum();
            let expected = if i == jj { n as i32 } else { 0 };
            if dot != expected {
                let what = if i == jj { "squared norm" } else { "inner product" };
                return Ok(Outcome::new(
                    false,
                    format!("rows {i} and {jj}: {what} {dot}, expected {expected}"),
                    &stats,
                ));
            }
        }
    }
    if want_symmetric {
        if let Some((i, jj)) = asym {
            return Ok(Outcome::new(
                false,
                format!("H H^T = nI holds but H is not symmetric: H[{i}][{jj}] != H[{jj}][{i}]"),
                &stats,
            ));
        }
    }
    let kind = if want_symmetric {
        "symmetric Hadamard matrix"
    } else {
        "Hadamard matrix"
    };
    Ok(Outcome::new(
        true,
        format!("{kind} of order {n} verified (H H^T = {n} I)"),
        &stats,
    ))
}

fn proper(n: usize, support: &[usize]) -> bool {
    if support.is_empty() {
        return false;
    }
    (2..=n)
        .filter(|e| n.is_multiple_of(*e))
        .all(|e| support.iter().any(|&s| s % e != support[0] % e))
}

pub fn check_circulant_weighing(j: &J) -> Res<Outcome> {
    let c = fields(
        j,
        "circulant_weighing_matrix",
        &["n", "weight", "first_row"],
        &["proper"],
    )?;
    let n = c.int("n", Some(1), Some(MAX_CW_ORDER))? as usize;
    let weight = c.int("weight", Some(1), Some(n as i64))? as usize;
    let want_proper = c.bool_or("proper", false)?;
    let row = int_list(c.req("first_row"), "field 'first_row'", Some(n), Some(-1), Some(1))?;
    let support: Vec<usize> = (0..n).filter(|&i| row[i] != 0).collect();
    let is_proper = proper(n, &support);
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("weight", support.len() as u64)
        .set("plus", row.iter().filter(|&&x| x == 1).count() as u64)
        .set("minus", row.iter().filter(|&&x| x == -1).count() as u64)
        .set("proper", is_proper);
    if support.len() != weight {
        return Ok(Outcome::new(
            false,
            format!(
                "the first row has {} nonzero entries, claimed weight {weight}",
                support.len()
            ),
            &stats,
        ));
    }
    // PAF(s) = sum_i a_i a_{i+s mod n}, accumulated over ordered pairs of support positions
    let mut paf = vec![0i64; n];
    for &i in &support {
        for &jj in &support {
            paf[(jj + n - i) % n] += row[i] * row[jj];
        }
    }
    if paf[0] != weight as i64 {
        return Ok(Outcome::new(
            false,
            format!("PAF(0) = {}, expected {weight}", paf[0]),
            &stats,
        ));
    }
    if let Some(s) = (1..n).find(|&s| paf[s] != 0) {
        return Ok(Outcome::new(
            false,
            format!("the periodic autocorrelation at shift {s} is {}, expected 0", paf[s]),
            &stats,
        ));
    }
    if want_proper && !is_proper {
        return Ok(Outcome::new(
            false,
            "the autocorrelation condition holds but the matrix is not proper: the support lies in one residue class modulo a divisor e > 1 of n",
            &stats,
        ));
    }
    Ok(Outcome::new(
        true,
        format!("circulant weighing matrix CW({n},{weight}) verified"),
        &stats,
    ))
}
