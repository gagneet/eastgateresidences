const fs = require('fs');
const path = require('path');

const RUN_ID_PATH = path.join(__dirname, '.test_run_id');

function getTestRunId() {
    if (process.env.TEST_RUN_ID) {
        return process.env.TEST_RUN_ID;
    }
    if (fs.existsSync(RUN_ID_PATH)) {
        return fs.readFileSync(RUN_ID_PATH, 'utf8').trim();
    }
    return 'test_run_unknown';
}

module.exports = {getTestRunId};
