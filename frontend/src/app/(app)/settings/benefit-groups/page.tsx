// @featuretrace:levy-fairness — configure which lots are compared with which.
// Layer: frontend
// Data flow: this page → /benefit-groups → core.benefit_groups + core.lot_benefit_groups
//            → levy_fairness_service group resolution (building-scoped).
// Related: backend/routers/benefit_groups.py
//          frontend/src/pages/dashboard/LevyFairnessPage.jsx
'use client';

import BenefitGroupsSettingsPage from '@/pages/dashboard/BenefitGroupsSettingsPage';

export default function Page() {
    return <BenefitGroupsSettingsPage/>;
}
