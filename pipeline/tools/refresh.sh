#!/bin/sh
# The morning refresh: collect, score, export, then commit the new data.
#
#   pipeline/tools/refresh.sh              run and commit locally
#   pipeline/tools/refresh.sh --publish    run, commit, and push (the site redeploys)
#   pipeline/tools/refresh.sh --no-collect re-score what is stored, for a dry run
#
# A warm run takes about ten minutes. New on-thesis companies are not ranked
# until they have an entry in pipeline/review.json; the report at the end
# lists the strongest of them.
set -eu

publish=0
args=""
for a in "$@"; do
  case "$a" in
    --publish) publish=1 ;;
    *) args="$args $a" ;;
  esac
done

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root/pipeline"

# shellcheck disable=SC2086  # args is a flag list, split on purpose
python3 -m antenna run $args
python3 -m antenna report -n 15

cd "$root"
git add web/src/data web/public/palette.json pipeline/review.json pipeline/briefs
if git diff --cached --quiet; then
  echo "Nothing changed since the last run."
  exit 0
fi
git commit -q -m "Run of $(date -u +%Y-%m-%d)"
echo "Committed: $(git log --oneline -1)"

if [ "$publish" -eq 1 ]; then
  git push -q origin main
  echo "Pushed. The site redeploys in about two minutes."
else
  echo "Not pushed. Run 'git push' or rerun with --publish to update the site."
fi
