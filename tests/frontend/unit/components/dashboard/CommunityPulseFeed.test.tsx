// @featuretrace:dashboard-v2 — Verifies CommunityPulseFeed action affordances in owner dashboard v2.
// Layer: test
// Data flow: OwnerDashboard → CommunityPulseFeed → /community navigation (building-scoped).
// Related: frontend/src/components/dashboard/CommunityPulseFeed.tsx

import { render, screen } from '@testing-library/react';
import CommunityPulseFeed from '@/components/dashboard/CommunityPulseFeed';

describe('CommunityPulseFeed', () => {
  it('renders each item View control as a real link to community page', () => {
    render(
      <CommunityPulseFeed
        items={[
          {
            id: 'item-1',
            title: 'AGM notice published',
            created_at: new Date().toISOString(),
            type: 'announcement',
          },
        ]}
      />,
    );

    const viewLink = screen.getByRole('link', { name: /view: agm notice published/i });
    expect(viewLink.getAttribute('href')).toBe('/community');
  });
});
