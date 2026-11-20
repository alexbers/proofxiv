//! `proofxiv-check <certificate_type> <file.json>`: the Rust implementation of the ProofXiv reference
//! checkers.  Prints exactly one JSON line `{"ok", "type", "details", "stats"}` and exits with
//! 0 (valid), 1 (invalid) or 2 (malformed input, unknown type, unreadable file, internal error).
//! The certificate formats are documented in `worker/checkers/FORMATS.md`.

// index loops are the clearest notation in the numeric kernels
#![allow(clippy::needless_range_loop, clippy::type_complexity)]

mod codes;
mod common;
mod geometry;
mod graphs;
mod json;
mod matrices;
mod poly;
mod ramsey;
mod vdw;

use common::{Outcome, Res};
use json::J;
use serde_json::{json, Map, Value};
use std::io::Write;

type Checker = fn(&J) -> Res<Outcome>;

const CHECKERS: &[(&str, Checker)] = &[
    ("graph_ramsey_coloring", ramsey::check_graph_ramsey),
    ("hypergraph_ramsey_coloring", ramsey::check_hypergraph_ramsey),
    ("vdw_coloring", vdw::check_vdw),
    ("linear_code", codes::check_linear_code),
    ("covering_code", codes::check_covering_code),
    ("regular_graph_girth", graphs::check_regular_graph_girth),
    ("hadamard_matrix", matrices::check_hadamard),
    ("circulant_weighing_matrix", matrices::check_circulant_weighing),
    ("affine_line_free_set", geometry::check_affine_line_free),
    ("kissing_configuration", geometry::check_kissing),
    ("heilbronn_configuration", geometry::check_heilbronn),
    ("polynomial_positive_interval", poly::check_polynomial),
];

fn emit(ok: bool, ctype: &str, details: &str, stats: Map<String, Value>) {
    let line = json!({"ok": ok, "type": ctype, "details": details, "stats": Value::Object(stats)});
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{line}");
    let _ = out.flush();
}

fn run(args: &[std::ffi::OsString]) -> i32 {
    if args.len() != 3 {
        emit(
            false,
            "",
            "usage: proofxiv-check <certificate_type> <file.json>",
            Map::new(),
        );
        return 2;
    }
    let ctype_owned = args[1].to_string_lossy().into_owned();
    let ctype = ctype_owned.as_str();
    let path_buf = std::path::PathBuf::from(&args[2]);
    let path_shown = path_buf.display().to_string();
    let path = path_shown.as_str();
    let Some(&(_, checker)) = CHECKERS.iter().find(|(name, _)| *name == ctype) else {
        let known: Vec<&str> = CHECKERS.iter().map(|(n, _)| *n).collect();
        emit(
            false,
            ctype,
            &format!("unknown certificate type {ctype:?}; known: {}", known.join(", ")),
            Map::new(),
        );
        return 2;
    };
    let bytes = match std::fs::read(&path_buf) {
        Ok(b) => b,
        Err(e) => {
            emit(false, ctype, &format!("cannot read {path}: {e}"), Map::new());
            return 2;
        }
    };
    let value = match json::parse(&bytes) {
        Ok(v) => v,
        Err(e) => {
            emit(false, ctype, &format!("malformed certificate: {e}"), Map::new());
            return 2;
        }
    };
    std::panic::set_hook(Box::new(|_| {}));
    match std::panic::catch_unwind(|| checker(&value)) {
        Ok(Ok(outcome)) => {
            emit(outcome.ok, ctype, &outcome.details, outcome.stats);
            if outcome.ok {
                0
            } else {
                1
            }
        }
        Ok(Err(common::Malformed(msg))) => {
            emit(false, ctype, &format!("malformed certificate: {msg}"), Map::new());
            2
        }
        Err(panic) => {
            let msg = panic
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| panic.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "panic".to_string());
            emit(false, ctype, &format!("internal error: {msg}"), Map::new());
            2
        }
    }
}

fn main() {
    let args: Vec<std::ffi::OsString> = std::env::args_os().collect();
    let code = run(&args);
    std::process::exit(code);
}
