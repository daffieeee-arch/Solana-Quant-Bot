#!/bin/bash
set -euo pipefail

echo "Deployment is disabled in this Gate A artifact." >&2
echo "TrueNAS application changes require a separate, explicit Gate B approval with fresh pre-write checks and rollback plan." >&2
exit 1
