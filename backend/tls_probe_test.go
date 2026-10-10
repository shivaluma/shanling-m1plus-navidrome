package main

import (
	"crypto/x509"
	"io"
	"net/http"
	"os"
	"testing"
)

// Opt-in: verify the supplied Mozilla bundle against the public server without
// credentials. An authentication rejection is expected and does not fail TLS.
func TestExplicitTLSBundleProbe(t *testing.T) {
	target, bundle := os.Getenv("NAVIDROME_TLS_PROBE_URL"), os.Getenv("NAVIDROME_CA_BUNDLE")
	if target == "" || bundle == "" {
		t.Skip("explicit public TLS probe not requested")
	}
	s, err := newService(Config{ServerURL: target, Username: "not-sent", Password: "not-sent"})
	if err != nil {
		t.Fatal("invalid public probe URL")
	}
	if s.base.Scheme != "https" {
		t.Fatal("TLS probe requires HTTPS")
	}
	pem, err := os.ReadFile(bundle)
	if err != nil {
		t.Fatal("cannot read probe CA bundle")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(pem) {
		t.Fatal("probe CA bundle invalid")
	}
	// Use only this bundle, excluding development-machine system/keychain roots.
	s.api.Transport.(*http.Transport).TLSClientConfig.RootCAs = roots
	u := *s.base
	u.Path = "/rest/ping.view"
	u.RawPath = ""
	res, err := s.api.Get(u.String()) // No Subsonic auth parameters or Authorization.
	if err != nil {
		t.Fatal("public server TLS probe failed")
	}
	defer res.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(res.Body, 65536))
	if res.TLS == nil || len(res.TLS.VerifiedChains) == 0 {
		t.Fatal("missing verified TLS chain")
	}
	t.Logf("credential-free ping: verified TLS, HTTP %d", res.StatusCode)
}
