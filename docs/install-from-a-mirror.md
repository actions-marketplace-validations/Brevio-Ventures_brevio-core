# Install from a mirror or a downloaded file

For a machine, a build or a network that does not reach PyPI or GitHub directly. brevio-core has no runtime dependencies
(`pyproject.toml` lists none) and is one pure-Python wheel, so there is one file to carry across and not a tree of
packages. These routes follow from how the package is built. The maintainers have not run them from a mirror, and the
README says the same of GitHub Enterprise Server. After the install, the commands in
[the security review](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md#what-each-command-sends-over-the-network) are the ones to check against your own
network policy.

## From your own package mirror

Point `pip` at the mirror's simple index and pin the version:

```sh
pip install --index-url <the simple index of your mirror> brevio-core==0.10.0
```

`pip` reads the same address from the `PIP_INDEX_URL` environment variable. The mirror needs this one package, because
brevio-core depends on no other.

## From a downloaded wheel

On a machine that reaches PyPI, or your mirror, download the wheel and write down its hash:

```sh
pip download --no-deps brevio-core==0.10.0 --dest wheels
pip hash wheels/brevio_core-0.10.0-py3-none-any.whl
```

PyPI keeps an attestation for the files of a release. Check it where you downloaded the wheel, with the command in
[the security review](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md#provenance). The check contacts PyPI, so it is not a step for the machine that
is cut off.

Carry the `wheels` folder across, then install from it and from nothing else:

```sh
pip install --no-index --no-deps wheels/brevio_core-0.10.0-py3-none-any.whl
```

To pin the hash, put the value `pip hash` printed in a requirements file and install with `--require-hashes`:

```sh
brevio-core==0.10.0 --hash=sha256:<the value pip hash printed>
pip install --require-hashes --no-index --no-deps --find-links wheels -r requirements.txt
```

## From the single file

A GitHub release carries `brevio-core.pyz` and its build-provenance attestation. Check it where you can reach GitHub,
then copy the file across and run it with Python 3.10 or newer:

```sh
gh attestation verify brevio-core.pyz --repo Brevio-Ventures/brevio-core
python3 brevio-core.pyz scan --diff
```

`gh attestation verify` asks GitHub for the attestation unless you give it a bundle and a trusted root; its own
documentation says how, and this page does not test that.

## In the GitHub Action

The Action installs the package with `pip`, so `pip`'s environment variables apply: set `PIP_INDEX_URL` in the job's `env`
to use a mirror. To install from a wheel you keep in your repository, check the repository out first and give the
Action's `source` input a path to it:

```yaml
- uses: actions/checkout@<the full commit SHA>
- uses: Brevio-Ventures/brevio-core@v0.10.0
  with:
    source: vendor/brevio_core-0.10.0-py3-none-any.whl
```

The Action also uses two actions from GitHub (`actions/setup-python` and `github/codeql-action/upload-sarif`), so the
runner must be allowed to fetch those, or your organisation must mirror them.
