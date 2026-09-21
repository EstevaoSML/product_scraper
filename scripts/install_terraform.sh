#!/usr/bin/env bash
set -euo pipefail
version=1.13.5
destination="${AGENT_TEMPDIRECTORY:-/tmp}/scraper-terraform"
mkdir -p "$destination"
archive="terraform_${version}_linux_amd64.zip"
curl --fail --silent --show-error --location "https://releases.hashicorp.com/terraform/${version}/${archive}" -o "$destination/$archive"
curl --fail --silent --show-error --location "https://releases.hashicorp.com/terraform/${version}/terraform_${version}_SHA256SUMS" -o "$destination/SHA256SUMS"
pushd "$destination" >/dev/null
grep " ${archive}$" SHA256SUMS | sha256sum --check --strict
unzip -o "$archive" terraform >/dev/null
popd >/dev/null
echo "##vso[task.prependpath]$destination"
