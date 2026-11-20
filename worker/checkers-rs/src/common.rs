//! Shared helpers: verdicts, field access with the documented validation rules, exact numbers.

use crate::json::J;
use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::{One, Zero};
use serde_json::{Map, Value};

pub const MAX_DIGITS: usize = 10_000;

/// The input does not follow its documented format (exit code 2).
#[derive(Debug)]
pub struct Malformed(pub String);

pub type Res<T> = Result<T, Malformed>;

pub fn malformed<T>(msg: impl Into<String>) -> Res<T> {
    Err(Malformed(msg.into()))
}

/// The verdict of a checker on a well-formed certificate.
pub struct Outcome {
    pub ok: bool,
    pub details: String,
    pub stats: Map<String, Value>,
}

impl Outcome {
    pub fn new(ok: bool, details: impl Into<String>, stats: &Stats) -> Outcome {
        Outcome {
            ok,
            details: details.into(),
            stats: stats.0.clone(),
        }
    }
}

/// Ordered builder for the `stats` object.
#[derive(Default, Clone)]
pub struct Stats(pub Map<String, Value>);

impl Stats {
    pub fn new() -> Stats {
        Stats(Map::new())
    }
    pub fn set(&mut self, key: &str, value: impl Into<Value>) -> &mut Stats {
        self.0.insert(key.to_string(), value.into());
        self
    }
    pub fn null(&mut self, key: &str) -> &mut Stats {
        self.0.insert(key.to_string(), Value::Null);
        self
    }
}

/// A validated certificate object: unknown keys are rejected, required keys are present.
pub struct Cert<'a> {
    items: &'a [(String, J)],
}

pub fn fields<'a>(j: &'a J, ctype: &str, required: &[&str], optional: &[&str]) -> Res<Cert<'a>> {
    let items = match j {
        J::Obj(items) => items,
        _ => return malformed("the certificate must be a JSON object"),
    };
    for (key, value) in items {
        let known =
            required.contains(&key.as_str()) || optional.contains(&key.as_str()) || key == "type" || key == "comment";
        if !known {
            return malformed(format!("unknown field {key:?}"));
        }
        if key == "type" && *value != J::Str(ctype.to_string()) {
            return malformed(format!("field 'type' must be {ctype:?}"));
        }
        if key == "comment" && !matches!(value, J::Str(_)) {
            return malformed("field 'comment' must be a string");
        }
    }
    for key in required {
        if !items.iter().any(|(k, _)| k == key) {
            return malformed(format!("missing field {key:?}"));
        }
    }
    Ok(Cert { items })
}

impl<'a> Cert<'a> {
    pub fn get(&self, key: &str) -> Option<&'a J> {
        self.items.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }
    pub fn req(&self, key: &str) -> &'a J {
        self.get(key).expect("required field checked in fields()")
    }
    pub fn int(&self, key: &str, lo: Option<i64>, hi: Option<i64>) -> Res<i64> {
        as_int(self.req(key), &format!("field {key:?}"), lo, hi)
    }
    pub fn opt_int(&self, key: &str, lo: Option<i64>, hi: Option<i64>) -> Res<Option<i64>> {
        match self.get(key) {
            None => Ok(None),
            Some(v) => as_int(v, &format!("field {key:?}"), lo, hi).map(Some),
        }
    }
    pub fn bool_or(&self, key: &str, default: bool) -> Res<bool> {
        match self.get(key) {
            None => Ok(default),
            Some(J::Bool(b)) => Ok(*b),
            Some(_) => malformed(format!("field {key:?} must be true or false")),
        }
    }
    pub fn str(&self, key: &str) -> Res<&'a str> {
        match self.req(key) {
            J::Str(s) => Ok(s),
            _ => malformed(format!("field {key:?} must be a string")),
        }
    }
    pub fn arr(&self, key: &str) -> Res<&'a [J]> {
        as_arr(self.req(key), &format!("field {key:?}"), None)
    }
}

pub fn as_int(j: &J, what: &str, lo: Option<i64>, hi: Option<i64>) -> Res<i64> {
    let v = match j {
        J::Int(v) => *v,
        _ => return malformed(format!("{what} must be an integer")),
    };
    if let Some(lo) = lo {
        if v < lo {
            return malformed(format!("{what} must be >= {lo}"));
        }
    }
    if let Some(hi) = hi {
        if v > hi {
            return malformed(format!("{what} must be <= {hi}"));
        }
    }
    Ok(v)
}

pub fn as_arr<'a>(j: &'a J, what: &str, len: Option<usize>) -> Res<&'a [J]> {
    match j {
        J::Arr(items) => {
            if let Some(len) = len {
                if items.len() != len {
                    return malformed(format!("{what} must have length {len}, got {}", items.len()));
                }
            }
            Ok(items)
        }
        _ => malformed(format!("{what} must be an array")),
    }
}

pub fn int_list(j: &J, what: &str, len: Option<usize>, lo: Option<i64>, hi: Option<i64>) -> Res<Vec<i64>> {
    let items = as_arr(j, what, len)?;
    items
        .iter()
        .enumerate()
        .map(|(i, x)| as_int(x, &format!("{what}[{i}]"), lo, hi))
        .collect()
}

fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

fn parse_signed_digits(s: &str) -> Option<BigInt> {
    let body = s.strip_prefix('-').unwrap_or(s);
    if !is_digits(body) || body.len() > MAX_DIGITS {
        return None;
    }
    let mag = BigInt::parse_bytes(body.as_bytes(), 10)?;
    Some(if s.starts_with('-') { -mag } else { mag })
}

/// An arbitrary-size integer: a JSON integer or a string `-?[0-9]+` (at most MAX_DIGITS digits).
#[allow(dead_code)]
pub fn parse_big_int(j: &J, what: &str) -> Res<BigInt> {
    match j {
        J::Int(v) => Ok(BigInt::from(*v)),
        J::Str(s) => parse_signed_digits(s).ok_or_else(|| {
            Malformed(format!(
                "{what} must be an integer or a decimal integer string of at most {MAX_DIGITS} digits"
            ))
        }),
        _ => malformed(format!("{what} must be an integer or a decimal integer string")),
    }
}

/// A rational number: a JSON integer or a string `-?[0-9]+` or `-?[0-9]+/[0-9]+`.
pub fn parse_rational(j: &J, what: &str) -> Res<BigRational> {
    let err = || {
        Malformed(format!(
            "{what} must be an integer or a string 'p' or 'p/q' (at most {MAX_DIGITS} digits each)"
        ))
    };
    match j {
        J::Int(v) => Ok(BigRational::from_integer(BigInt::from(*v))),
        J::Str(s) => {
            let (num, den) = match s.split_once('/') {
                Some((a, b)) => (a, Some(b)),
                None => (s.as_str(), None),
            };
            let num = parse_signed_digits(num).ok_or_else(err)?;
            let den = match den {
                None => BigInt::one(),
                Some(d) => {
                    if !is_digits(d) || d.len() > MAX_DIGITS {
                        return Err(err());
                    }
                    BigInt::parse_bytes(d.as_bytes(), 10).ok_or_else(err)?
                }
            };
            if den.is_zero() {
                return malformed(format!("{what} has a zero denominator"));
            }
            Ok(BigRational::new(num, den))
        }
        _ => Err(err()),
    }
}

pub fn rational_str(x: &BigRational) -> String {
    if x.denom().is_one() {
        x.numer().to_string()
    } else {
        format!("{}/{}", x.numer(), x.denom())
    }
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn b64_encode(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let v = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(B64[((v >> (18 - 6 * i)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// Strict standard base64 (RFC 4648 alphabet, '=' padding); only the canonical encoding is accepted.
pub fn decode_base64(s: &str, what: &str) -> Res<Vec<u8>> {
    let bytes = s.as_bytes();
    let bad = || Malformed(format!("{what} is not valid canonical base64"));
    if !bytes.len().is_multiple_of(4) {
        return Err(bad());
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for (ci, chunk) in bytes.chunks(4).enumerate() {
        let last = ci + 1 == bytes.len() / 4;
        let mut v: u32 = 0;
        let mut pad = 0;
        for (i, &c) in chunk.iter().enumerate() {
            let d = match c {
                b'A'..=b'Z' => c - b'A',
                b'a'..=b'z' => c - b'a' + 26,
                b'0'..=b'9' => c - b'0' + 52,
                b'+' => 62,
                b'/' => 63,
                b'=' if last && i >= 2 => {
                    pad += 1;
                    0
                }
                _ => return Err(bad()),
            };
            if pad > 0 && c != b'=' {
                return Err(bad());
            }
            v = (v << 6) | u32::from(d);
        }
        let produced = 3 - pad;
        for i in 0..produced {
            out.push(((v >> (16 - 8 * i)) & 0xFF) as u8);
        }
    }
    if b64_encode(&out) != s {
        return Err(bad());
    }
    Ok(out)
}

pub fn binom(n: u64, k: u64) -> u128 {
    if k > n {
        return 0;
    }
    let k = k.min(n - k);
    let mut r: u128 = 1;
    for i in 0..k {
        r = r * u128::from(n - i) / u128::from(i + 1);
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_roundtrip() {
        for data in [&b""[..], b"a", b"ab", b"abc", b"abcd", &[0xff, 0x00, 0x10]] {
            let enc = b64_encode(data);
            assert_eq!(decode_base64(&enc, "x").unwrap(), data);
        }
        assert!(decode_base64("AB==", "x").is_err()); // non-canonical trailing bits
        assert!(decode_base64("AA==", "x").is_ok());
        assert!(decode_base64("AA=", "x").is_err());
        assert!(decode_base64("A===", "x").is_err());
        assert!(decode_base64("AA==AA==", "x").is_err());
        assert!(decode_base64("AA\n=", "x").is_err());
    }

    #[test]
    fn rationals() {
        let r = parse_rational(&J::Str("-6/4".into()), "x").unwrap();
        assert_eq!(rational_str(&r), "-3/2");
        assert!(parse_rational(&J::Str("1/0".into()), "x").is_err());
        assert!(parse_rational(&J::Str("+1".into()), "x").is_err());
        assert!(parse_rational(&J::Str("1.5".into()), "x").is_err());
        assert!(parse_rational(&J::Str("-1/-2".into()), "x").is_err());
        assert!(parse_rational(&J::Str("".into()), "x").is_err());
        assert_eq!(rational_str(&parse_rational(&J::Int(-7), "x").unwrap()), "-7");
    }

    #[test]
    fn binomials() {
        assert_eq!(binom(97, 4), 3_464_840);
        assert_eq!(binom(5, 7), 0);
        assert_eq!(binom(60, 30), 118_264_581_564_861_424);
    }
}
