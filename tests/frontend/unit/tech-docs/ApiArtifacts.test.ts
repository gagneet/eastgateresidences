import fs from 'fs';
import path from 'path';

const techDocsRoot = path.resolve(process.cwd(), 'public', 'tech-docs');

function readJson(relativePath: string) {
    return JSON.parse(fs.readFileSync(path.join(techDocsRoot, relativePath), 'utf8'));
}

function flattenItems(items: any[]): any[] {
    return items.flatMap((item) => item.item ? flattenItems(item.item) : [item]);
}

describe('generated public API artifacts', () => {
    it('keeps Postman artifacts in the public tech-docs path', () => {
        expect(fs.existsSync(path.join(techDocsRoot, 'postman', 'strataos_demo_collection.json'))).toBe(true);
        expect(fs.existsSync(path.join(techDocsRoot, 'postman', 'strataos_demo_environment.json'))).toBe(true);
    });

    it('documents dashboard finance routes in OpenAPI and Postman', () => {
        const openapi = readJson('strataos-openapi.json');
        const collection = readJson('postman/strataos_demo_collection.json');
        const requests = flattenItems(collection.item);
        const names = requests.map((item) => item.name);

        expect(openapi.paths['/api/finance/building-overview']).toBeTruthy();
        expect(openapi.paths['/api/finance/unit-dashboard-overview/{unit_number}']).toBeTruthy();
        expect(openapi.paths['/api/analytics/my-streak']).toBeTruthy();
        expect(openapi.paths['/api/analytics/dashboard-v2-extras']).toBeTruthy();

        expect(names.some((name) => name.includes('/api/finance/building-overview'))).toBe(true);
        expect(names.some((name) => name.includes('/api/finance/unit-dashboard-overview/{unit_number}'))).toBe(true);
        expect(names.some((name) => name.includes('/api/analytics/my-streak'))).toBe(true);
        expect(names.some((name) => name.includes('/api/analytics/dashboard-v2-extras'))).toBe(true);
    });

    it('uses X-API-Key for partner-facing External API v1 requests', () => {
        const collection = readJson('postman/strataos_demo_collection.json');
        const requests = flattenItems(collection.item);
        const buildingRequest = requests.find((item) => item.name.includes('/api/v1/building -'));
        const apiKeysRequest = requests.find((item) => item.name.includes('/api/v1/api-keys -'));
        const healthRequest = requests.find((item) => item.name.includes('/api/v1/health -'));
        const scopesRequest = requests.find((item) => item.name.includes('/api/v1/scopes -'));

        expect(buildingRequest.request.auth).toEqual({type: 'noauth'});
        expect(buildingRequest.request.header).toEqual(
            expect.arrayContaining([expect.objectContaining({key: 'X-API-Key', value: '{{api_key}}'})])
        );
        expect(apiKeysRequest.request.auth.type).toBe('bearer');
        expect(healthRequest.request.auth).toEqual({type: 'noauth'});
        expect(healthRequest.request.header).not.toEqual(
            expect.arrayContaining([expect.objectContaining({key: 'X-API-Key'})])
        );
        expect(scopesRequest.request.auth).toEqual({type: 'noauth'});
        expect(scopesRequest.request.header).toEqual(
            expect.arrayContaining([expect.objectContaining({key: 'X-API-Key', value: '{{api_key}}'})])
        );
    });

    it('publishes the canonical owner registry in the tech-docs index', () => {
        const indexHtml = fs.readFileSync(path.join(techDocsRoot, 'index.html'), 'utf8');
        const registryHtml = fs.readFileSync(path.join(techDocsRoot, 'canonical-owners.html'), 'utf8');

        expect(indexHtml).toContain('href="canonical-owners.html"');
        expect(indexHtml).toContain('Canonical Owners');
        expect(registryHtml).toContain('Canonical Owner Registry');
        expect(registryHtml).toContain('data-language="python"');
        expect(registryHtml).toContain('data-language="javascript"');
        expect(registryHtml).toContain('api-error-detail');
    });
});
