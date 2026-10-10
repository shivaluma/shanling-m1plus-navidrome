package main

import "strings"

// nfc composes decomposed accents for display. The device renderer has no
// shaping, so separate combining marks would overlap; Vietnamese needs up to two
// marks per letter (for example o + horn + hook above). Input is kept unchanged
// when it has no combining diacritic, and unmatched marks are left in place.
func nfc(s string) string {
	if !strings.ContainsFunc(s, isMark) {
		return s
	}
	out := make([]rune, 0, len(s))
	starter := -1
	for _, r := range s {
		if isMark(r) && starter == len(out)-1 && starter >= 0 {
			if c, ok := nfcPairs[[2]rune{out[starter], r}]; ok {
				out[starter] = c
				continue
			}
		}
		out = append(out, r)
		if isMark(r) {
			starter = -1
		} else {
			starter = len(out) - 1
		}
	}
	return string(out)
}

func isMark(r rune) bool { return r >= 0x300 && r <= 0x36f }
