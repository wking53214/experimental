#!/bin/sh
# Download a few machines of the Server Machine Dataset (Su et al., KDD 2019; OmniAnomaly repo, MIT license).
# 38 server metrics per machine, labeled anomaly segments. Not committed: ~10 MB per file.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/smd
base=https://raw.githubusercontent.com/NetManAIOps/OmniAnomaly/master/ServerMachineDataset
for m in "${@:-machine-1-1 machine-2-1 machine-3-1 machine-1-6}"; do
  for part in train test test_label; do
    [ -s "data/smd/${part}_${m}.txt" ] || curl -sSf -o "data/smd/${part}_${m}.txt" "$base/$part/$m.txt"
  done
done
