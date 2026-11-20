//! Strict JSON value used for certificates (see FORMATS.md, "Common conventions").
//!
//! * numbers must be integers in the signed 64-bit range (fractions, exponents and larger
//!   integers are rejected; big numbers are written as strings);
//! * duplicate keys in an object are rejected;
//! * nesting deeper than `MAX_DEPTH` containers is rejected.

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::collections::HashSet;
use std::fmt;

pub const MAX_DEPTH: usize = 64;

#[derive(Debug, Clone, PartialEq)]
pub enum J {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl J {
    /// Nesting depth: scalars 0, containers 1 + the depth of the deepest element.
    pub fn depth(&self) -> usize {
        match self {
            J::Arr(items) => 1 + items.iter().map(J::depth).max().unwrap_or(0),
            J::Obj(items) => 1 + items.iter().map(|(_, v)| v.depth()).max().unwrap_or(0),
            _ => 0,
        }
    }

    pub fn get(&self, key: &str) -> Option<&J> {
        match self {
            J::Obj(items) => items.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
}

impl<'de> Deserialize<'de> for J {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<J, D::Error> {
        deserializer.deserialize_any(JVisitor)
    }
}

struct JVisitor;

impl<'de> Visitor<'de> for JVisitor {
    type Value = J;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value")
    }
    fn visit_bool<E>(self, v: bool) -> Result<J, E> {
        Ok(J::Bool(v))
    }
    fn visit_i64<E>(self, v: i64) -> Result<J, E> {
        Ok(J::Int(v))
    }
    fn visit_u64<E: de::Error>(self, v: u64) -> Result<J, E> {
        i64::try_from(v)
            .map(J::Int)
            .map_err(|_| E::custom("JSON integer literal out of the signed 64-bit range"))
    }
    fn visit_i128<E: de::Error>(self, _v: i128) -> Result<J, E> {
        Err(E::custom("JSON integer literal out of the signed 64-bit range"))
    }
    fn visit_u128<E: de::Error>(self, _v: u128) -> Result<J, E> {
        Err(E::custom("JSON integer literal out of the signed 64-bit range"))
    }
    fn visit_f64<E: de::Error>(self, _v: f64) -> Result<J, E> {
        Err(E::custom(
            "non-integer JSON number (or integer out of the signed 64-bit range) is not allowed",
        ))
    }
    fn visit_str<E>(self, v: &str) -> Result<J, E> {
        Ok(J::Str(v.to_owned()))
    }
    fn visit_string<E>(self, v: String) -> Result<J, E> {
        Ok(J::Str(v))
    }
    fn visit_unit<E>(self) -> Result<J, E> {
        Ok(J::Null)
    }
    fn visit_none<E>(self) -> Result<J, E> {
        Ok(J::Null)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<J, A::Error> {
        let mut out = Vec::new();
        while let Some(item) = seq.next_element::<J>()? {
            out.push(item);
        }
        Ok(J::Arr(out))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<J, A::Error> {
        let mut out: Vec<(String, J)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom(format!("duplicate key {key:?} in a JSON object")));
            }
            let value = map.next_value::<J>()?;
            out.push((key, value));
        }
        Ok(J::Obj(out))
    }
}

/// Parse a certificate file.
pub fn parse(bytes: &[u8]) -> Result<J, String> {
    let text = std::str::from_utf8(bytes).map_err(|_| "file is not valid UTF-8".to_string())?;
    let value: J = serde_json::from_str(text).map_err(|e| format!("invalid JSON: {e}"))?;
    if value.depth() > MAX_DEPTH {
        return Err(format!("JSON nesting deeper than {MAX_DEPTH}"));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integers_only() {
        assert_eq!(
            parse(b"[1, -2, 0]").unwrap(),
            J::Arr(vec![J::Int(1), J::Int(-2), J::Int(0)])
        );
        assert!(parse(b"-0").is_err()); // serde_json reads -0 as a float; Python rejects it too
        assert!(parse(b"1.0").is_err());
        assert!(parse(b"1e3").is_err());
        assert!(parse(b"9223372036854775808").is_err());
        assert_eq!(parse(b"-9223372036854775808").unwrap(), J::Int(i64::MIN));
        assert!(parse(b"-9223372036854775809").is_err());
        assert!(parse(b"123456789012345678901234567890").is_err());
    }

    #[test]
    fn duplicates_and_depth() {
        assert!(parse(br#"{"a": 1, "a": 2}"#).is_err());
        assert!(parse(br#"{"a": {"b": 1}, "b": {"b": 2}}"#).is_ok());
        let deep = "[".repeat(65) + &"]".repeat(65);
        assert!(parse(deep.as_bytes()).is_err());
        let ok = "[".repeat(64) + &"]".repeat(64);
        assert!(parse(ok.as_bytes()).is_ok());
    }

    #[test]
    fn strings() {
        assert!(parse(br#""\ud800""#).is_err());
        assert_eq!(parse(b"\"\\u00e9\"").unwrap(), J::Str("\u{e9}".into()));
        assert!(parse(b"\xef\xbb\xbf{}").is_err());
        assert!(parse(b"NaN").is_err());
    }
}
