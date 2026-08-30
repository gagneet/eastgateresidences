/**
 * Barrel for the dashboard "premium" widgets that are still live.
 *
 * GAP-UI-001 removed 12 unreachable components from this directory. Reachability
 * was proved with an import-graph walk from every `src/app` entry point, with
 * barrel re-exports stripped — a grep is not sufficient here, because consumers
 * import this barrel by relative path as well as by `@/` alias.
 *
 * Every component here now renders with recharts + shadcn primitives; the
 * @tremor/react dependency was removed in GAP-UI-001 Phase 2. Do not reintroduce
 * a second chart or component library — chart colours come from lib/chartTheme.
 */
export {default as MarketPulseCard} from './MarketPulseCard';
export {default as CommunityActivityCard} from './CommunityActivityCard';
export {default as UtilityComparisonCard} from './UtilityComparisonCard';
export {default as ActivityFeedPremium} from './ActivityFeedPremium';
export {default as MetricCard} from './MetricCard';
export {default as CountUp} from './CountUp';
