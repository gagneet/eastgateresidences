import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 3},
                {duration: '30s', target: 3},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        // Forecasting and time-series endpoints are the most expensive
        'http_req_duration{endpoint:sinking_fund_forecast}': ['p(95)<2000'],
        'http_req_duration{endpoint:fund_forecasts}': ['p(95)<2000'],
        'http_req_duration{endpoint:financial_health}': ['p(95)<1500'],
        'http_req_duration{endpoint:compliance_summary}': ['p(95)<1500'],
        'http_req_duration{endpoint:maintenance_stats}': ['p(95)<1000'],
        'http_req_duration{endpoint:expense_breakdown}': ['p(95)<1000'],
        'http_req_duration{endpoint:spend_trend}': ['p(95)<1000'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';

const forecastErrors = new Counter('analytics_forecast_errors');
const forecastDuration = new Trend('sinking_fund_forecast_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            'X-Building-ID': BUILDING_ID,
        },
    };
}

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for analytics benchmarks');
    }
}

export default function () {
    const h = headers();

    group('Forecasting Endpoints', () => {
        // 10-year projection — most expensive analytics endpoint
        const sfForecast = http.get(`${BASE_URL}/analytics/sinking-fund-forecast?years=10`, {
            ...h,
            tags: {endpoint: 'sinking_fund_forecast'},
        });
        check(sfForecast, {
            'sinking-fund-forecast 200': (r) => r.status === 200,
        }) || forecastErrors.add(1);
        forecastDuration.add(sfForecast.timings.duration);

        const fundForecasts = http.get(`${BASE_URL}/analytics/fund-forecasts`, {
            ...h,
            tags: {endpoint: 'fund_forecasts'},
        });
        check(fundForecasts, {'fund-forecasts 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);
    });

    sleep(0.5);

    group('Health & Compliance', () => {
        const health = http.get(`${BASE_URL}/analytics/financial-health`, {
            ...h,
            tags: {endpoint: 'financial_health'},
        });
        check(health, {'financial-health 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);

        const compliance = http.get(`${BASE_URL}/analytics/compliance-summary`, {
            ...h,
            tags: {endpoint: 'compliance_summary'},
        });
        check(compliance, {'compliance-summary 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);
    });

    sleep(0.5);

    group('Maintenance Analytics', () => {
        // $facet aggregation — runs 3 pipeline stages in one query
        const stats = http.get(`${BASE_URL}/analytics/maintenance-stats`, {
            ...h,
            tags: {endpoint: 'maintenance_stats'},
        });
        check(stats, {'maintenance-stats 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);

        const spendTrend = http.get(`${BASE_URL}/analytics/maintenance/spend-trend`, {
            ...h,
            tags: {endpoint: 'spend_trend'},
        });
        check(spendTrend, {'spend-trend 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);

        const expenses = http.get(`${BASE_URL}/analytics/expense-breakdown`, {
            ...h,
            tags: {endpoint: 'expense_breakdown'},
        });
        check(expenses, {'expense-breakdown 200': (r) => r.status === 200}) ||
        forecastErrors.add(1);
    });

    sleep(1);
}

export function teardown() {
    // Read-only benchmark; no test records are created.
}
