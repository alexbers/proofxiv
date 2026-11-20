//! `regular_graph_girth`: simple, d-regular, girth exactly g.
//!
//! Girth: breadth-first search from every vertex s; a non-tree edge {x, y} closes a closed walk of
//! length dist(x) + dist(y) + 1 containing a cycle, and for s on a shortest cycle the minimum is
//! exactly the girth.  A search stops once 2 dist(x) + 1 >= best.

use crate::common::{as_arr, fields, malformed, Outcome, Res, Stats};
use crate::json::J;
use std::collections::VecDeque;

const MAX_N: i64 = 10_000_000;
const MAX_EDGES: usize = 50_000_000;

fn girth(n: usize, offsets: &[usize], targets: &[u32]) -> Option<u64> {
    const UNSEEN: u32 = u32::MAX;
    let mut best = u64::MAX;
    let mut dist = vec![UNSEEN; n];
    let mut parent = vec![UNSEEN; n];
    let mut touched: Vec<usize> = Vec::new();
    let mut queue: VecDeque<usize> = VecDeque::new();
    for s in 0..n {
        dist[s] = 0;
        parent[s] = UNSEEN;
        touched.push(s);
        queue.push_back(s);
        while let Some(x) = queue.pop_front() {
            let dx = u64::from(dist[x]);
            if 2 * dx + 1 >= best {
                break;
            }
            for &y in &targets[offsets[x]..offsets[x + 1]] {
                let y = y as usize;
                if y as u32 == parent[x] {
                    continue;
                }
                if dist[y] == UNSEEN {
                    dist[y] = dist[x] + 1;
                    parent[y] = x as u32;
                    touched.push(y);
                    queue.push_back(y);
                } else {
                    best = best.min(dx + u64::from(dist[y]) + 1);
                }
            }
        }
        queue.clear();
        for &v in &touched {
            dist[v] = UNSEEN;
            parent[v] = UNSEEN;
        }
        touched.clear();
    }
    (best != u64::MAX).then_some(best)
}

pub fn check_regular_graph_girth(j: &J) -> Res<Outcome> {
    let c = fields(j, "regular_graph_girth", &["n", "degree", "girth", "edges"], &[])?;
    let n = c.int("n", Some(1), Some(MAX_N))? as usize;
    let degree = c.int("degree", Some(0), None)?;
    let claimed = c.int("girth", Some(3), None)?;
    let edges_j = c.arr("edges")?;
    if edges_j.len() > MAX_EDGES {
        return malformed(format!("more than {MAX_EDGES} edges"));
    }
    let mut edges: Vec<(usize, usize)> = Vec::with_capacity(edges_j.len());
    for (i, e) in edges_j.iter().enumerate() {
        let pair = match as_arr(e, &format!("edges[{i}]"), Some(2)) {
            Ok(p) => p,
            Err(_) => return malformed(format!("edges[{i}] must be a pair of integers")),
        };
        let (J::Int(u), J::Int(v)) = (&pair[0], &pair[1]) else {
            return malformed(format!("edges[{i}] must be a pair of integers"));
        };
        if *u < 0 || *u >= n as i64 || *v < 0 || *v >= n as i64 {
            return malformed(format!("edges[{i}] has an endpoint outside 0..{}", n - 1));
        }
        edges.push((*u as usize, *v as usize));
    }
    let m = edges.len();
    let mut deg = vec![0u64; n];
    for &(u, v) in &edges {
        deg[u] += 1;
        deg[v] += 1;
    }
    let min_deg = *deg.iter().min().unwrap();
    let max_deg = *deg.iter().max().unwrap();
    let mut stats = Stats::new();
    stats
        .set("n", n as u64)
        .set("edges", m as u64)
        .set("simple", false)
        .set("min_degree", min_deg)
        .set("max_degree", max_deg)
        .null("girth");
    if let Some(i) = edges.iter().position(|&(u, v)| u == v) {
        return Ok(Outcome::new(
            false,
            format!("edges[{i}] is a loop at vertex {}", edges[i].0),
            &stats,
        ));
    }
    let mut keyed: Vec<(usize, usize, usize)> = edges
        .iter()
        .enumerate()
        .map(|(i, &(u, v))| (u.min(v), u.max(v), i))
        .collect();
    keyed.sort_unstable();
    if let Some(w) = keyed.windows(2).find(|w| w[0].0 == w[1].0 && w[0].1 == w[1].1) {
        let (a, b) = (w[0].2.min(w[1].2), w[0].2.max(w[1].2));
        return Ok(Outcome::new(
            false,
            format!("edges[{a}] and edges[{b}] are the same edge {{{}, {}}}", w[0].0, w[0].1),
            &stats,
        ));
    }
    stats.set("simple", true);
    let mut offsets = vec![0usize; n + 1];
    for &(u, v) in &edges {
        offsets[u + 1] += 1;
        offsets[v + 1] += 1;
    }
    for i in 0..n {
        offsets[i + 1] += offsets[i];
    }
    let mut fill = offsets.clone();
    let mut targets = vec![0u32; 2 * m];
    for &(u, v) in &edges {
        targets[fill[u]] = v as u32;
        fill[u] += 1;
        targets[fill[v]] = u as u32;
        fill[v] += 1;
    }
    let g = girth(n, &offsets, &targets);
    match g {
        Some(g) => stats.set("girth", g),
        None => stats.null("girth"),
    };
    if min_deg != degree as u64 || max_deg != degree as u64 {
        let bad = deg.iter().position(|&d| d != degree as u64).unwrap();
        return Ok(Outcome::new(
            false,
            format!("vertex {bad} has degree {}, claimed degree {degree}", deg[bad]),
            &stats,
        ));
    }
    match g {
        None => Ok(Outcome::new(
            false,
            format!("the graph is acyclic, claimed girth {claimed}"),
            &stats,
        )),
        Some(g) if g != claimed as u64 => Ok(Outcome::new(
            false,
            format!("the girth is {g}, claimed {claimed}"),
            &stats,
        )),
        Some(g) => Ok(Outcome::new(
            true,
            format!("simple {degree}-regular graph on {n} vertices with girth {g} verified"),
            &stats,
        )),
    }
}
