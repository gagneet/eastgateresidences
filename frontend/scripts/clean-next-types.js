const fs = require('fs');
const path = require('path');

const frontendRoot = path.resolve(__dirname, '..');
const generatedTypeDirs = ['.next/types', '.next/dev/types'];

// Route deletions can leave validator imports for files that no longer exist.
// Do not clear .next/cache here; this hook only removes generated type surfaces.
for (const relativePath of generatedTypeDirs) {
  const target = path.join(frontendRoot, relativePath);

  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true });
    console.log(`Removed stale Next.js generated types: ${relativePath}`);
  }
}
