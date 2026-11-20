//! `graph_ramsey_coloring` and `hypergraph_ramsey_coloring`.

use crate::common::{binom, decode_base64, fields, int_list, malformed, Outcome, Res, Stats};
use crate::json::J;

const MAX_GRAPH_N: i64 = 4096;
const MAX_HYPER_N: i64 = 1024;
const MAX_SUBSETS: u128 = 1 << 27;

// ------------------------------------------------------------------------------------------
// graph_ramsey_coloring: clique search with explicit candidate lists (vertices in increasing
// order), adjacency read from the n x n colour matrix.
// ------------------------------------------------------------------------------------------

fn clique_search(n: usize, mat: &[u8], colour: u8, size: usize) -> Option<Vec<usize>> {
    fn rec(n: usize, mat: &[u8], colour: u8, size: usize, clique: &mut Vec<usize>, cands: &[usize]) -> bool {
        if clique.len() == size {
            return true;
        }
        for (idx, &v) in cands.iter().enumerate() {
            if clique.len() + (cands.len() - idx) < size {
                return false;
            }
            let row = &mat[v * n..(v + 1) * n];
            let next: Vec<usize> = cands[idx + 1..].iter().copied().filter(|&u| row[u] == colour).collect();
            clique.push(v);
            if rec(n, mat, colour, size, clique, &next) {
                return true;
            }
            clique.pop();
        }
        false
    }
    let mut clique = Vec::new();
    let all: Vec<usize> = (0..n).collect();
    if rec(n, mat, colour, size, &mut clique, &all) {
        Some(clique)
    } else {
        None
    }
}

pub fn check_graph_ramsey(j: &J) -> Res<Outcome> {
    let c = fields(
        j,
        "graph_ramsey_coloring",
        &["n", "colours", "forbidden_clique_sizes", "edge_colours"],
        &[],
    )?;
    let n = c.int("n", Some(0), Some(MAX_GRAPH_N))? as usize;
    let colours = c.int("colours", Some(1), Some(10))? as usize;
    let sizes = int_list(
        c.req("forbidden_clique_sizes"),
        "field 'forbidden_clique_sizes'",
        Some(colours),
        Some(1),
        None,
    )?;
    let text = c.str("edge_colours")?;
    let m = n * n.saturating_sub(1) / 2;
    if text.chars().count() != m {
        return malformed(format!("field 'edge_colours' must have length n(n-1)/2 = {m}"));
    }
    let bytes = text.as_bytes();
    if let Some(bad) = bytes.iter().find(|&&b| b < b'0' || b >= b'0' + colours as u8) {
        return malformed(format!(
            "field 'edge_colours' contains byte {bad:#x}, expected digits 0..{}",
            colours - 1
        ));
    }
    let mut mat = vec![u8::MAX; n * n];
    let mut counts = vec![0u64; colours];
    let mut r = 0usize;
    for jv in 1..n {
        for iv in 0..jv {
            let col = bytes[r] - b'0';
            r += 1;
            mat[iv * n + jv] = col;
            mat[jv * n + iv] = col;
            counts[col as usize] += 1;
        }
    }
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("colours", colours as u64)
        .set("edges_per_colour", counts.clone());
    for (col, &s) in sizes.iter().enumerate() {
        let s = s as u64;
        if s > n as u64 {
            continue;
        }
        if let Some(clique) = clique_search(n, &mat, col as u8, s as usize) {
            return Ok(Outcome::new(
                false,
                format!("colour {col} contains a clique of size {s}: vertices {clique:?}"),
                &stats,
            ));
        }
    }
    let shown: Vec<String> = sizes.iter().map(|s| s.to_string()).collect();
    Ok(Outcome::new(
        true,
        format!(
            "no colour-i clique of size s_i for any i; the colouring of K_{n} shows R({}) > {n}",
            shown.join(", ")
        ),
        &stats,
    ))
}

// ------------------------------------------------------------------------------------------
// hypergraph_ramsey_coloring
// ------------------------------------------------------------------------------------------

struct Links {
    words: usize,
    data: [Vec<u64>; 2], // data[c][rank * words + w]: bitset of u with (T + u) of colour c
}

/// Build the link bitsets by walking all k-subsets in colex order.
fn build_links(n: usize, k: usize, bit: &dyn Fn(usize) -> bool, table: &[Vec<u64>]) -> Links {
    let words = n.div_ceil(64);
    let rows = binom(n as u64, (k - 1) as u64) as usize;
    let mut data = [vec![0u64; rows * words], vec![0u64; rows * words]];
    let mut comb: Vec<usize> = (0..k).collect();
    let mut rank = 0usize;
    loop {
        let col = usize::from(bit(rank));
        for t in 0..k {
            // colex rank of comb without position t
            let mut r = 0u64;
            for (i, &a) in comb.iter().enumerate() {
                if i < t {
                    r += table[a][i + 1];
                } else if i > t {
                    r += table[a][i];
                }
            }
            let u = comb[t];
            data[col][r as usize * words + u / 64] |= 1u64 << (u % 64);
        }
        rank += 1;
        // colex successor
        let mut i = 0;
        while i < k {
            let limit = if i + 1 < k { comb[i + 1] } else { n };
            if comb[i] + 1 < limit {
                comb[i] += 1;
                for (t, slot) in comb.iter_mut().enumerate().take(i) {
                    *slot = t;
                }
                break;
            }
            i += 1;
        }
        if i == k {
            break;
        }
    }
    Links { words, data }
}

struct MonoSearch<'a> {
    n: usize,
    k: usize,
    size: usize,
    words: usize,
    link: &'a [u64],
    table: &'a [Vec<u64>],
}

impl MonoSearch<'_> {
    /// `cand`: vertices > max(current) that keep every k-subset monochromatic;
    /// `partial[t]`: partial colex ranks (sum C(a_i, i)) of the t-subsets of `current`.
    fn extend(&self, current: &mut Vec<usize>, cand: &[u64], partial: &[Vec<u64>]) -> bool {
        if current.len() == self.size {
            return true;
        }
        let need = self.size - current.len();
        let mut rest = cand.to_vec();
        loop {
            let available: u32 = rest.iter().map(|w| w.count_ones()).sum();
            if (available as usize) < need {
                return false;
            }
            let Some(wi) = rest.iter().position(|&w| w != 0) else {
                return false;
            };
            let bitpos = rest[wi].trailing_zeros() as usize;
            let v = wi * 64 + bitpos;
            rest[wi] &= rest[wi] - 1;
            if need == 1 {
                current.push(v);
                return true;
            }
            let mut next = rest.clone();
            if current.len() + 2 >= self.k {
                let bv = self.table[v][self.k - 1];
                for &pr in &partial[self.k - 2] {
                    let off = (pr + bv) as usize * self.words;
                    let row = &self.link[off..off + self.words];
                    for (x, y) in next.iter_mut().zip(row) {
                        *x &= *y;
                    }
                }
            }
            let count: u32 = next.iter().map(|w| w.count_ones()).sum();
            if (count as usize) < need - 1 {
                continue;
            }
            let mut new_partial: Vec<Vec<u64>> = partial.to_vec();
            for t in (1..=(self.k - 2).min(current.len() + 1)).rev() {
                let bvt = self.table[v][t];
                let extra: Vec<u64> = partial[t - 1].iter().map(|r| r + bvt).collect();
                new_partial[t].extend(extra);
            }
            current.push(v);
            if self.extend(current, &next, &new_partial) {
                return true;
            }
            current.pop();
        }
    }
}

pub fn check_hypergraph_ramsey(j: &J) -> Res<Outcome> {
    let c = fields(
        j,
        "hypergraph_ramsey_coloring",
        &["n", "k", "forbidden_sizes", "colouring"],
        &[],
    )?;
    let n = c.int("n", Some(1), Some(MAX_HYPER_N))? as usize;
    let k = c.int("k", Some(2), Some(8))? as usize;
    if k > n {
        return malformed("field 'k' must not exceed n");
    }
    let sizes = int_list(
        c.req("forbidden_sizes"),
        "field 'forbidden_sizes'",
        Some(2),
        Some(1),
        None,
    )?;
    let total = binom(n as u64, k as u64);
    if total > MAX_SUBSETS {
        return malformed(format!("C(n, k) = {total} exceeds the limit {MAX_SUBSETS}"));
    }
    let total = total as usize;
    let text = match c.req("colouring") {
        J::Str(s) => s.as_str(),
        _ => return malformed("field 'colouring' must be a base64 string"),
    };
    let raw = decode_base64(text, "field 'colouring'")?;
    if raw.len() != total.div_ceil(8) {
        return malformed(format!(
            "field 'colouring' must decode to ceil(C(n,k)/8) = {} bytes",
            total.div_ceil(8)
        ));
    }
    let bit = |r: usize| (raw[r / 8] >> (7 - (r % 8))) & 1 == 1;
    for r in total..raw.len() * 8 {
        if bit(r) {
            return malformed("padding bits after the last k-subset must be zero");
        }
    }
    let ones = (0..total).filter(|&r| bit(r)).count();
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("k", k as u64)
        .set("colour_counts", vec![(total - ones) as u64, ones as u64]);

    // table[x][j] = C(x, j) for x <= n, j <= k
    let table: Vec<Vec<u64>> = (0..=n)
        .map(|x| (0..=k).map(|jj| binom(x as u64, jj as u64) as u64).collect())
        .collect();
    let mut links: Option<Links> = None;
    for colour in 0..2usize {
        let s = sizes[colour] as u64;
        if s > n as u64 {
            continue;
        }
        let s = s as usize;
        let found = if s < k {
            Some((0..s).collect::<Vec<usize>>())
        } else {
            let l = links.get_or_insert_with(|| build_links(n, k, &bit, &table));
            let search = MonoSearch {
                n,
                k,
                size: s,
                words: l.words,
                link: &l.data[colour],
                table: &table,
            };
            let mut all = vec![0u64; l.words];
            for v in 0..search.n {
                all[v / 64] |= 1 << (v % 64);
            }
            let mut partial: Vec<Vec<u64>> = vec![Vec::new(); k - 1];
            partial[0].push(0);
            let mut current = Vec::new();
            if search.extend(&mut current, &all, &partial) {
                Some(current)
            } else {
                None
            }
        };
        if let Some(set) = found {
            return Ok(Outcome::new(
                false,
                format!("the {s}-set {set:?} has all its {k}-subsets of colour {colour}"),
                &stats,
            ));
        }
    }
    Ok(Outcome::new(
        true,
        format!(
            "no {}-set with all {k}-subsets of colour 0 and no {}-set with all {k}-subsets of colour 1; shows R({}, {}; {k}) > {n}",
            sizes[0], sizes[1], sizes[0], sizes[1]
        ),
        &stats,
    ))
}
