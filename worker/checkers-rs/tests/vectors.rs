//! Runs `proofxiv-check` on every shared hand-made test vector in `worker/checkers/testdata` and
//! checks the exit code expected for its directory (pass = 0, fail = 1, malformed = 2) and the
//! shape of the printed JSON line.  The real-data vectors are exercised by the Python test suite
//! (`worker/tests/test_checkers.py -m slow`), which also compares the two implementations.

use std::path::{Path, PathBuf};
use std::process::Command;

fn testdata() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("checkers")
        .join("testdata")
}

#[test]
fn shared_vectors() {
    let binary = env!("CARGO_BIN_EXE_proofxiv-check");
    let mut checked = 0;
    let mut failures = Vec::new();
    let mut types: Vec<PathBuf> = std::fs::read_dir(testdata())
        .expect("testdata directory")
        .map(|e| e.unwrap().path())
        .collect();
    types.sort();
    for type_dir in types.iter().filter(|p| p.is_dir()) {
        let ctype = type_dir.file_name().unwrap().to_str().unwrap().to_string();
        for (category, expected) in [("pass", 0), ("fail", 1), ("malformed", 2)] {
            let dir = type_dir.join(category);
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries {
                let path = entry.unwrap().path();
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let out = Command::new(binary)
                    .arg(&ctype)
                    .arg(&path)
                    .output()
                    .expect("run proofxiv-check");
                let code = out.status.code().unwrap_or(-1);
                let stdout = String::from_utf8_lossy(&out.stdout);
                let lines: Vec<&str> = stdout.lines().collect();
                let line_ok = lines.len() == 1
                    && serde_json::from_str::<serde_json::Value>(lines[0]).is_ok_and(|v| {
                        v.get("ok") == Some(&serde_json::Value::Bool(code == 0))
                            && v.get("type").and_then(|t| t.as_str()) == Some(ctype.as_str())
                            && v.get("details").is_some_and(|d| d.is_string())
                            && v.get("stats").is_some_and(|s| s.is_object())
                    });
                if code != expected || !line_ok {
                    failures.push(format!(
                        "{}: exit {code}, expected {expected}: {stdout}",
                        path.display()
                    ));
                }
                checked += 1;
            }
        }
    }
    assert!(checked >= 200, "only {checked} vectors found");
    assert!(
        failures.is_empty(),
        "{} failures:\n{}",
        failures.len(),
        failures.join("\n")
    );
}

#[test]
fn usage_and_unknown_type() {
    let binary = env!("CARGO_BIN_EXE_proofxiv-check");
    let out = Command::new(binary).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    let any = testdata().join("vdw_coloring").join("pass").join("W23_gt_8.json");
    let out = Command::new(binary).arg("no_such_type").arg(&any).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    let out = Command::new(binary).arg("vdw_coloring").arg(&any).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
}
