Model files (GGUF) are **not** stored in the repository. They ship as a separate
`ia-models-<tier>.zip` (manifest.json + .gguf) and are installed from
Settings → AI → Import model package. The engine refuses any model whose sha256 does not match
the manifest or whose licence is not Apache-2.0 / MIT. See docs/admin-guide.md.
