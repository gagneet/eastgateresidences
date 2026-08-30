const fs = require('fs');
const path = require('path');

const REGISTRY_PATH = path.join(__dirname, '.cleanup_registry.ndjson');

function registerCleanup(entry) {
    if (!entry || !entry.collection || !entry.field) {
        return;
    }
    const line = `${JSON.stringify(entry)}\n`;
    fs.appendFileSync(REGISTRY_PATH, line, 'utf8');
}

function getRegistryPath() {
    return REGISTRY_PATH;
}

module.exports = {registerCleanup, getRegistryPath};
