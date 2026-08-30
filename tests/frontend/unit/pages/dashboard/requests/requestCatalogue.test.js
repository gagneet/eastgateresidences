import {
    findRequestDefinition,
    hydrateRequestCatalogue,
    isRequestEligible,
    REQUEST_CATALOGUE,
    searchRequestCatalogue,
    trackRequestCatalogueEvent,
} from '@/config/requestCatalogue';

describe('request catalogue', () => {
    it('preserves legacy tab aliases', () => {
        expect(findRequestDefinition('pet')?.id).toBe('pet');
        expect(findRequestDefinition('remote')?.id).toBe('access-control');
        expect(findRequestDefinition('unknown')).toBeUndefined();
    });

    it('filters catalogue entries by effective role', () => {
        const tenantEntries = REQUEST_CATALOGUE.filter((definition) =>
            isRequestEligible(definition, 'tenant')
        );
        const managerEntries = REQUEST_CATALOGUE.filter((definition) =>
            isRequestEligible(definition, 'strata_manager')
        );

        expect(tenantEntries.map(({id}) => id)).toContain('maintenance');
        expect(tenantEntries.map(({id}) => id)).not.toContain('work-order');
        expect(managerEntries.map(({id}) => id)).toContain('work-order');
        expect(isRequestEligible(findRequestDefinition('alterations'), 'real_estate_agent')).toBe(true);
        expect(isRequestEligible(findRequestDefinition('alterations'), 'agent')).toBe(false);
        expect(isRequestEligible(findRequestDefinition('work-order'), 'admin_staff')).toBe(true);
    });

    it('hydrates only known server-authorised definitions', () => {
        const hydrated = hydrateRequestCatalogue([
            {
                key: 'pet',
                title: 'Building pet application',
                search_terms: ['animal'],
                expected_response: '3 business days',
                legacy_tab_aliases: ['pet'],
            },
            {key: 'unknown-server-workflow', title: 'Unknown'},
        ]);

        expect(hydrated).toHaveLength(1);
        expect(hydrated[0].id).toBe('pet');
        expect(hydrated[0].title).toBe('Building pet application');
        expect(hydrated[0].searchTerms).toEqual(['animal']);
        expect(hydrated[0].component).toBeDefined();
    });

    it('searches titles, descriptions, categories and synonyms', () => {
        expect(searchRequestCatalogue(REQUEST_CATALOGUE, 'leak').map(({id}) => id))
            .toContain('maintenance');
        expect(searchRequestCatalogue(REQUEST_CATALOGUE, 'garage').map(({id}) => id))
            .toContain('access-control');
        expect(searchRequestCatalogue(REQUEST_CATALOGUE, 'insurance').map(({id}) => id))
            .toEqual(expect.arrayContaining(['insurance-claim', 'insurance-enquiry']));
    });

    it('returns all authorised definitions for an empty search', () => {
        expect(searchRequestCatalogue(REQUEST_CATALOGUE, '  ')).toBe(REQUEST_CATALOGUE);
    });

    it('emits only allowlisted analytics metadata', () => {
        window.dataLayer = [];
        const listener = jest.fn();
        window.addEventListener('strataos:analytics', listener);

        trackRequestCatalogueEvent('request_catalogue_searched', {
            building_id: 'TEST-REQUEST-A',
            role: 'owner',
            result: 'changed',
            search_text: 'leaking pipe should never be logged',
            description: 'free form text should never be logged',
        });

        expect(window.dataLayer).toEqual([
            {
                event: 'request_catalogue_searched',
                role: 'owner',
                result: 'changed',
            },
        ]);
        expect(listener.mock.calls[0][0].detail).not.toHaveProperty('building_id');
        expect(listener.mock.calls[0][0].detail).not.toHaveProperty('search_text');
        expect(listener.mock.calls[0][0].detail).not.toHaveProperty('description');
        window.removeEventListener('strataos:analytics', listener);
        delete window.dataLayer;
    });
});
