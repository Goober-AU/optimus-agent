//! Port of packages/ai/src/utils/sanitize-unicode.ts

/// Removes unpaired Unicode surrogate characters from UTF-16 code units.
///
/// Unpaired surrogates (high surrogates 0xD800-0xDBFF without matching low
/// surrogates 0xDC00-0xDFFF, or vice versa) cause JSON serialization errors in
/// many API providers. Valid pairs are preserved.
pub fn sanitize_surrogate_units(units: &[u16]) -> String {
    let mut out: Vec<u16> = Vec::with_capacity(units.len());
    let mut i = 0usize;
    while i < units.len() {
        let unit = units[i];
        let is_high = (0xD800..=0xDBFF).contains(&unit);
        let is_low = (0xDC00..=0xDFFF).contains(&unit);
        if is_high {
            let next_is_low = units.get(i + 1).map(|n| (0xDC00..=0xDFFF).contains(n)).unwrap_or(false);
            if next_is_low {
                out.push(unit);
                out.push(units[i + 1]);
                i += 2;
                continue;
            }
            i += 1;
            continue;
        }
        if is_low {
            // A low surrogate that was not consumed as part of a pair is unpaired.
            i += 1;
            continue;
        }
        out.push(unit);
        i += 1;
    }
    String::from_utf16_lossy(&out)
}

/// `sanitizeSurrogates(text: string): string`.
///
/// Rust `&str`/`String` values are valid UTF-8 and therefore cannot hold
/// unpaired surrogates: `encode_utf16` on valid UTF-8 always emits matched
/// surrogate pairs, `sanitize_surrogate_units` copies matched pairs through
/// untouched, and `String::from_utf16_lossy` is lossless on valid UTF-16. The
/// historical `encode_utf16` -> `sanitize_surrogate_units` ->
/// `from_utf16_lossy` pipeline was therefore the identity for every possible
/// input, so this function now takes the cheap path: one owned clone, no
/// intermediate `Vec<u16>` allocations and no full rescan. Parity is pinned by
/// the `parity_*` tests below; raw UTF-16 input (where unpaired surrogates are
/// actually possible) goes through `sanitize_surrogate_units`.
pub fn sanitize_surrogates(text: &str) -> String {
    text.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_valid_emoji() {
        assert_eq!(sanitize_surrogates("Hello \u{1F648} World"), "Hello \u{1F648} World");
    }

    #[test]
    fn removes_unpaired_high_surrogate() {
        let units: Vec<u16> = "Text ".encode_utf16().chain([0xD83D]).chain(" here".encode_utf16()).collect();
        assert_eq!(sanitize_surrogate_units(&units), "Text  here");
    }

    #[test]
    fn removes_unpaired_low_surrogate() {
        let units: Vec<u16> = [0xDE00].into_iter().chain("tail".encode_utf16()).collect();
        assert_eq!(sanitize_surrogate_units(&units), "tail");
    }

    #[test]
    fn keeps_paired_surrogates() {
        let units: Vec<u16> = vec![0xD83D, 0xDE48];
        assert_eq!(sanitize_surrogate_units(&units), "\u{1F648}");
    }

    /// The pre-optimization pipeline of `sanitize_surrogates`, kept verbatim as
    /// the parity oracle for the cheap path.
    fn legacy_utf16_roundtrip(text: &str) -> String {
        let units: Vec<u16> = text.encode_utf16().collect();
        sanitize_surrogate_units(&units)
    }

    #[test]
    fn parity_empty_and_ascii() {
        for case in ["", "a", "plain ascii 123 \n\t", "\u{0}", "\u{7F}"] {
            assert_eq!(sanitize_surrogates(case), case);
            assert_eq!(sanitize_surrogates(case), legacy_utf16_roundtrip(case));
        }
    }

    #[test]
    fn parity_unicode_edge_cases() {
        let cases: Vec<String> = vec![
            String::new(),
            "\u{0}".to_string(),                    // NUL
            "\u{7F}".to_string(),                   // DEL
            "\u{80}".to_string(),                   // first 2-byte code point
            "\u{7FF}".to_string(),                  // last 2-byte code point
            "\u{800}".to_string(),                  // first 3-byte code point
            "\u{FFFF}".to_string(),                  // last 3-byte code point / last BMP
            "\u{D7FF}".to_string(),                 // just below surrogate range
            "\u{E000}".to_string(),                 // just above surrogate range
            "\u{10000}".to_string(),                 // first astral (surrogate-pair) code point
            "\u{10FFFF}".to_string(),                // maximum scalar value
            "\u{FFFD}".to_string(),                  // replacement character
            "\u{FEFF}".to_string(),                  // BOM
            "中文字符集测试".to_string(),              // CJK
            "\u{3042}\u{3099}".to_string(),         // kana + combining dakuten
            "e\u{0301}cole".to_string(),             // combining acute accent
            "\u{1F648}".to_string(),                 // 4-byte emoji
            "\u{1F600}\u{1F64F}\u{1F926}\u{200D}\u{2642}\u{FE0F}".to_string(), // ZWJ sequence
            "\u{1D400}\u{1D7FF}".to_string(),       // mathematical alphanumerics
            "\u{10400}\u{104B1}".to_string(),       // deseret (surrogate pairs)
            "mixed \u{4E2D}\u{6587} \u{1F648} \u{0} tail \u{0301}".to_string(),
            "line\nbreaks\r\nand\ttabs".to_string(),
        ];
        for case in &cases {
            assert_eq!(sanitize_surrogates(case), *case, "identity failed for {case:?}");
            assert_eq!(sanitize_surrogates(case), legacy_utf16_roundtrip(case), "parity failed for {case:?}");
        }
    }

    #[test]
    fn parity_deterministic_fuzz() {
        // xorshift64* with a fixed seed: deterministic across runs and platforms.
        fn next(state: &mut u64) -> u64 {
            *state ^= *state >> 12;
            *state ^= *state << 25;
            *state ^= *state >> 27;
            state.wrapping_mul(0x2545F4914F6CDD1D)
        }
        let mut state: u64 = 0x9E3779B97F4A7C15;
        for _ in 0..2000 {
            let len = (next(&mut state) % 64) as usize;
            let mut s = String::new();
            for _ in 0..len {
                // Draw valid scalar values only (never inside the surrogate range).
                let c = loop {
                    let v = (next(&mut state) % 0x110000) as u32;
                    if (0xD800..=0xDFFF).contains(&v) {
                        continue;
                    }
                    break char::from_u32(v).unwrap();
                };
                s.push(c);
            }
            assert_eq!(sanitize_surrogates(&s), s, "identity failed for {s:?}");
            assert_eq!(sanitize_surrogates(&s), legacy_utf16_roundtrip(&s), "parity failed for {s:?}");
        }
    }

    #[test]
    fn parity_long_multibyte_string() {
        let big = "\u{4E2D}\u{1F648}a\u{0301}\u{0}".repeat(20_000);
        assert_eq!(sanitize_surrogates(&big), big);
        assert_eq!(sanitize_surrogates(&big), legacy_utf16_roundtrip(&big));
    }
}
