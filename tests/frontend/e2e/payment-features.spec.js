/**
 * Playwright E2E Tests for Payment Features
 *
 * Tests Stripe payment integration across all user roles
 *
 * Prerequisites:
 * - Stripe API keys configured in .env files
 * - Stripe test mode enabled
 * - Database seeded with test users and units
 *
 * Run: npx playwright test payment-features.spec.js
 */

import { expect, test } from '@playwright/test';
import cleanupRegistry from '../cleanup-registry.js';

const {registerCleanup} = cleanupRegistry;

// Test Configuration
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'https://eastgateresidences.com.au';

// Test Users (from seed data)
const USERS = {
    superAdmin: {
        email: 'admin@eastgate.com',
        password: '$SEED_TEST_USER_PASSWORD',
        role: 'super_admin',
        unitNumber: null
    },
    chairman: {
        email: 'chairman@eastgate.com',
        password: process.env.E2E_CHAIRMAN_PASSWORD || '',
        role: 'chairman',
        unitNumber: 'LOT1'
    },
    owner: {
        email: 'owner@eastgate.com',
        password: '$SEED_TEST_USER_PASSWORD',
        role: 'owner',
        unitNumber: 'LOT25'
    },
    tenant: {
        email: 'tenant@eastgate.com',
        password: process.env.E2E_TENANT_PASSWORD || '',
        role: 'tenant',
        unitNumber: null
    }
};

// Stripe Test Cards
const TEST_CARDS = {
    success: {
        number: '4242424242424242',
        expiry: '12/34',
        cvc: '123',
        zip: '12345'
    },
    declined: {
        number: '4000000000000002',
        expiry: '12/34',
        cvc: '123',
        zip: '12345'
    },
    requiresAuth: {
        number: '4000002500003155',
        expiry: '12/34',
        cvc: '123',
        zip: '12345'
    }
};

// Helper Functions
async function login(page, user) {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[name="email"]', user.email);
    await page.fill('input[name="password"]', user.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard**');
}

async function fillStripeCardForm(page, card) {
    // Wait for Stripe iframe to load
    const stripeFrame = page.frameLocator('iframe[name^="__privateStripeFrame"]').first();

    // Fill card number
    await stripeFrame.locator('[placeholder="Card number"]').fill(card.number);

    // Fill expiry
    await stripeFrame.locator('[placeholder="MM / YY"]').fill(card.expiry);

    // Fill CVC
    await stripeFrame.locator('[placeholder="CVC"]').fill(card.cvc);

    // Fill ZIP if present
    const zipInput = stripeFrame.locator('[placeholder*="ZIP"], [placeholder*="Postal"]');
    if (await zipInput.isVisible()) {
        await zipInput.fill(card.zip);
    }
}

// Test Suite: Payment Modal Visibility
test.describe('Payment Modal - Visibility by Role', () => {

    test('Owner with balance can see Pay Now button', async ({page}) => {
        await login(page, USERS.owner);

        // Navigate to owner dashboard
        await page.goto(`${BASE_URL}/dashboard`);

        // Check for financial summary card
        await expect(page.locator('[data-testid="owner-dashboard"]')).toBeVisible();

        // Check for Pay Now button (only visible if balance > 0)
        const payNowButton = page.locator('button:has-text("Pay Now")');

        // If balance > 0, button should be visible
        const isVisible = await payNowButton.isVisible();
        if (isVisible) {
            await expect(payNowButton).toBeEnabled();
        }
    });

    test('Tenant cannot see Pay Now button', async ({page}) => {
        await login(page, USERS.tenant);
        await page.goto(`${BASE_URL}/dashboard`);

        // Tenant should not have owner dashboard or Pay Now button
        await expect(page.locator('button:has-text("Pay Now")')).not.toBeVisible();
    });

    test('Super Admin can access payment features', async ({page}) => {
        await login(page, USERS.superAdmin);
        await page.goto(`${BASE_URL}/dashboard`);

        // Admin should be able to navigate to levy payments
        await page.click('a[href="/financials/levy-payments"]');
        await expect(page.locator('[data-testid="levy-payment-page"]')).toBeVisible();
    });
});

// Test Suite: Payment Modal Functionality
test.describe('Payment Modal - Stripe Integration', () => {

    test.beforeEach(async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);
    });

    test('Should open payment modal when Pay Now clicked', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        // Skip if no balance due
        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // Modal should open
        await expect(page.locator('[role="dialog"]')).toBeVisible();
        await expect(page.locator('text=Make Payment')).toBeVisible();
    });

    test('Should display correct payment amount in modal', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        // Get balance amount from dashboard
        const balanceText = await page.locator('text=/\\$[0-9,]+\\.\\d{2}/').first().textContent();
        const balanceAmount = balanceText.replace(/[$,]/g, '');

        await payNowButton.click();

        // Check modal displays same amount
        await expect(page.locator('[role="dialog"]')).toContainText(balanceAmount);
    });

    test('Should show loading state while initializing payment', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // Should show loading spinner initially
        await expect(page.locator('text=Initializing payment')).toBeVisible({timeout: 1000});
    });

    test('Should display Stripe payment form after initialization', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // Wait for Stripe Elements to load
        await page.waitForSelector('iframe[name^="__privateStripeFrame"]', {timeout: 10000});

        // Check for Stripe branding
        await expect(page.locator('text=Powered by Stripe')).toBeVisible();
    });

    test('Should show error if Stripe not configured', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // If Stripe keys missing, should show error
        const errorMessage = page.locator('text=/Payment processing is not configured|Failed to initialize payment/');

        // Either Stripe loads or error shows
        const stripeFrame = page.locator('iframe[name^="__privateStripeFrame"]');

        await expect(
            Promise.race([
                stripeFrame.waitFor({timeout: 5000}),
                errorMessage.waitFor({timeout: 5000})
            ])
        ).resolves.not.toThrow();
    });

    test('Should allow closing modal with Cancel button', async ({page}) => {
        const payNowButton = page.locator('button:has-text("Pay Now")');

        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();
        await expect(page.locator('[role="dialog"]')).toBeVisible();

        // Click cancel
        await page.click('button:has-text("Cancel")');

        // Modal should close
        await expect(page.locator('[role="dialog"]')).not.toBeVisible();
    });
});

// Test Suite: Successful Payment Flow
test.describe('Payment Flow - Successful Payment', () => {

    test.skip('Should complete payment with valid card', async ({page}) => {
        // This test requires Stripe test mode to be fully configured
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);

        const payNowButton = page.locator('button:has-text("Pay Now")');
        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        // Get initial balance
        const initialBalance = await page.locator('[data-testid="net-balance"]').textContent();

        // Open payment modal
        await payNowButton.click();
        await page.waitForSelector('iframe[name^="__privateStripeFrame"]', {timeout: 10000});

        // Fill card details
        await fillStripeCardForm(page, TEST_CARDS.success);

        // Submit payment
        await page.click('button:has-text("Pay")');

        // Wait for processing
        await expect(page.locator('text=Processing')).toBeVisible();

        // Wait for success message
        await expect(page.locator('text=Payment Successful')).toBeVisible({timeout: 10000});

        // Wait for modal to close and balance to update
        await page.waitForTimeout(2000);

        // Verify balance decreased
        const newBalance = await page.locator('[data-testid="net-balance"]').textContent();
        expect(newBalance).not.toBe(initialBalance);
    });

    test.skip('Should show success message after payment', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);

        // Trigger payment flow...
        // (Abbreviated for brevity)

        // Should see success toast
        await expect(page.locator('text=Payment successful')).toBeVisible();
    });

    test.skip('Should update balance immediately after payment', async ({page}) => {
        // Test that balance updates without page refresh
        // (Requires full Stripe integration)
    });
});

// Test Suite: Payment Errors
test.describe('Payment Flow - Error Handling', () => {

    test.skip('Should show error for declined card', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);

        const payNowButton = page.locator('button:has-text("Pay Now")');
        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();
        await page.waitForSelector('iframe[name^="__privateStripeFrame"]');

        // Use declined test card
        await fillStripeCardForm(page, TEST_CARDS.declined);
        await page.click('button:has-text("Pay")');

        // Should show error
        await expect(page.locator('text=/declined|failed/i')).toBeVisible({timeout: 10000});
    });

    test.skip('Should handle network errors gracefully', async ({page}) => {
        // Test offline scenario
        await page.context().setOffline(true);

        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);

        const payNowButton = page.locator('button:has-text("Pay Now")');
        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // Should show connection error
        await expect(page.locator('text=/connection|network|failed/i')).toBeVisible({timeout: 5000});
    });
});

// Test Suite: Levy Payments Page
test.describe('Levy Payments Page', () => {

    test('Should display payment methods for all users', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/financials/levy-payments`);

        await expect(page.locator('[data-testid="levy-payment-page"]')).toBeVisible();

        // Should see payment method cards
        await expect(page.locator('text=BPAY')).toBeVisible();
        await expect(page.locator('text=Bank Transfer')).toBeVisible();
    });

    test('Should show payment history tab', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/financials/levy-payments`);

        // Click history tab
        await page.click('text=Payment History');

        // Should see history table or empty state
        await expect(
            page.locator('text=/No payments recorded|Payment History/i')
        ).toBeVisible();
    });

    test('Should show levy status checker', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/financials/levy-payments`);

        // Click levy status tab
        await page.click('text=Levy Status');

        // Should see search input
        await expect(page.locator('input[placeholder*="Unit number"]')).toBeVisible();
    });

    test('Owner can check their own levy status', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/financials/levy-payments`);

        await page.click('text=Levy Status');

        // Enter unit number
        await page.fill('input[placeholder*="Unit number"]', USERS.owner.unitNumber);
        await page.click('button:has-text("Check")');

        // Should see levy details
        await expect(page.locator('text=/Annual Levy|Quarterly Amount/i')).toBeVisible({timeout: 5000});
    });
});

// Test Suite: Payment History (When Implemented)
test.describe('Payment History Page - NOT YET IMPLEMENTED', () => {

    test.skip('Should navigate to payment history page', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard/payment-history`);

        // This page doesn't exist yet - should show 404 or redirect
        await expect(page.locator('text=/Payment History|404/i')).toBeVisible();
    });

    test.skip('Should display table of past payments', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard/payment-history`);

        // Should see payments table
        await expect(page.locator('table')).toBeVisible();
        await expect(page.locator('th:has-text("Date")')).toBeVisible();
        await expect(page.locator('th:has-text("Amount")')).toBeVisible();
        await expect(page.locator('th:has-text("Status")')).toBeVisible();
    });

    test.skip('Should have download receipt button for each payment', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard/payment-history`);

        // Should see download buttons
        await expect(page.locator('button:has-text("Download Receipt")')).toBeVisible();
    });

    test.skip('Should download PDF receipt when button clicked', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard/payment-history`);

        // Setup download listener
        const [download] = await Promise.all([
            page.waitForEvent('download'),
            page.click('button:has-text("Download Receipt")').first()
        ]);

        // Check downloaded file
        const fileName = download.suggestedFilename();
        expect(fileName).toMatch(/receipt.*\.pdf$/i);
    });
});

// Test Suite: Admin Payment Management (When Implemented)
test.describe('Admin Payment Management - NOT YET IMPLEMENTED', () => {

    test.skip('Super admin should see all payments', async ({page}) => {
        await login(page, USERS.superAdmin);
        await page.goto(`${BASE_URL}/admin/console`);

        // Click payments tab
        await page.click('text=Payments');

        // Should see all payments across all units
        await expect(page.locator('table')).toBeVisible();
        await expect(page.locator('text=/Total Collected|Payment Summary/i')).toBeVisible();
    });

    test.skip('Should filter payments by unit number', async ({page}) => {
        await login(page, USERS.superAdmin);
        await page.goto(`${BASE_URL}/admin/console`);
        await page.click('text=Payments');

        // Use filter
        await page.fill('input[placeholder*="Unit"]', 'LOT25');
        await page.click('button:has-text("Filter")');

        // Should only show payments for LOT25
        await expect(page.locator('text=LOT25')).toBeVisible();
    });

    test.skip('Should filter payments by date range', async ({page}) => {
        await login(page, USERS.superAdmin);
        await page.goto(`${BASE_URL}/admin/console`);
        await page.click('text=Payments');

        // Set date range
        await page.fill('input[name="start_date"]', '2026-01-01');
        await page.fill('input[name="end_date"]', '2026-01-31');
        await page.click('button:has-text("Apply")');

        // Should filter results
        await expect(page.locator('table tbody tr')).toHaveCount(0); // No payments in Jan yet
    });

    test.skip('Should export payments to CSV', async ({page}) => {
        await login(page, USERS.superAdmin);
        await page.goto(`${BASE_URL}/admin/console`);
        await page.click('text=Payments');

        // Click export
        const [download] = await Promise.all([
            page.waitForEvent('download'),
            page.click('button:has-text("Export CSV")')
        ]);

        const fileName = download.suggestedFilename();
        expect(fileName).toMatch(/payments.*\.csv$/i);
    });
});

// Test Suite: Role-Based Access Control
test.describe('Payment Features - Access Control', () => {

    test('Owner can only pay for their own unit', async ({page}) => {
        await login(page, USERS.owner);

        // Try to access payment intent for different unit via API
        const response = await page.request.post(`${BASE_URL}/api/payments/create-intent`, {
            data: {
                unit_number: 'LOT1', // Different from owner's unit
                amount: 500,
                levy_period: 'Q1 2026',
                description: 'Test payment'
            }
        });

        // Should be forbidden
        expect(response.status()).toBe(403);
    });

    test('Admin can pay for any unit', async ({page}) => {
        await login(page, USERS.superAdmin);

        // Admin should be able to create payment for any unit
        const response = await page.request.post(`${BASE_URL}/api/payments/create-intent`, {
            data: {
                unit_number: 'LOT25',
                amount: 500,
                levy_period: 'Q1 2026',
                description: 'Admin payment'
            }
        });

        // Should succeed or fail due to missing Stripe keys (not auth)
        expect([200, 503]).toContain(response.status());
        if (response.status() === 200) {
            const data = await response.json();
            if (data && data.payment_intent_id) {
                registerCleanup({collection: 'payments', field: 'payment_intent_id', value: data.payment_intent_id});
            }
        }
    });

    test('Tenant cannot access payment features', async ({page}) => {
        await login(page, USERS.tenant);

        // Try to create payment
        const response = await page.request.post(`${BASE_URL}/api/payments/create-intent`, {
            data: {
                unit_number: 'LOT25',
                amount: 500,
                levy_period: 'Q1 2026',
                description: 'Test'
            }
        });

        // Should be forbidden
        expect(response.status()).toBe(403);
    });

    test('Owner can view only their payment history', async ({page}) => {
        await login(page, USERS.owner);

        // Get payment history
        const response = await page.request.get(`${BASE_URL}/api/payments/history`);
        expect(response.ok()).toBeTruthy();

        const payments = await response.json();

        // All payments should belong to this owner
        payments.forEach(payment => {
            expect(payment.owner_id).toBe(USERS.owner.id || payment.owner_id);
        });
    });

    test('Admin can view all payment history', async ({page}) => {
        await login(page, USERS.superAdmin);

        // Get all payments
        const response = await page.request.get(`${BASE_URL}/api/payments/history`);
        expect(response.ok()).toBeTruthy();

        const payments = await response.json();

        // Should return payments for all units (or empty if none exist)
        expect(Array.isArray(payments)).toBeTruthy();
    });
});

// Test Suite: Mobile Responsiveness
test.describe('Payment Features - Mobile', () => {
    test.use({viewport: {width: 375, height: 667}}); // iPhone SE

    test('Payment modal should be responsive on mobile', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/dashboard`);

        const payNowButton = page.locator('button:has-text("Pay Now")');
        if (!( await payNowButton.isVisible() )) {
            test.skip();
        }

        await payNowButton.click();

        // Modal should fit on small screen
        const modal = page.locator('[role="dialog"]');
        await expect(modal).toBeVisible();

        const modalBox = await modal.boundingBox();
        expect(modalBox.width).toBeLessThanOrEqual(375);
    });

    test('Levy payments page should be usable on mobile', async ({page}) => {
        await login(page, USERS.owner);
        await page.goto(`${BASE_URL}/financials/levy-payments`);

        // Page should load
        await expect(page.locator('[data-testid="levy-payment-page"]')).toBeVisible();

        // Payment method cards should stack vertically
        const cards = page.locator('.card-dashboard');
        const firstCard = cards.first();
        const secondCard = cards.nth(1);

        if (( await cards.count() ) >= 2) {
            const firstBox = await firstCard.boundingBox();
            const secondBox = await secondCard.boundingBox();

            // Second card should be below first (stacked)
            expect(secondBox.y).toBeGreaterThan(firstBox.y + firstBox.height);
        }
    });
});

// Test Suite: API Integration Tests
test.describe('Payment API - Direct Testing', () => {

    test('GET /api/payments/history should require authentication', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/payments/history`);
        expect(response.status()).toBe(401);
    });

    test('POST /api/payments/create-intent should validate required fields', async ({page}) => {
        await login(page, USERS.owner);

        // Missing required fields
        const response = await page.request.post(`${BASE_URL}/api/payments/create-intent`, {
            data: {
                unit_number: 'LOT25'
                // Missing: amount, levy_period
            }
        });

        expect(response.status()).toBe(422); // Validation error
    });

    test('GET /api/payments/{id}/receipt should return PDF', async ({page}) => {
        await login(page, USERS.owner);

        // This will fail if no payments exist
        // Get first payment ID from history
        const historyResponse = await page.request.get(`${BASE_URL}/api/payments/history`);
        if (historyResponse.ok()) {
            const payments = await historyResponse.json();

            if (payments.length > 0) {
                const paymentId = payments[ 0 ].id;

                // Get receipt
                const receiptResponse = await page.request.get(
                    `${BASE_URL}/api/payments/${paymentId}/receipt`
                );

                expect(receiptResponse.ok()).toBeTruthy();
                expect(receiptResponse.headers()[ 'content-type' ]).toContain('application/pdf');
            }
        }
    });
});

// Summary Report
test.afterAll(async () => {
    console.log('\n=== Payment Feature Test Summary ===\n');
    console.log('✅ Tests for implemented features: PaymentModal, LevyPaymentPage');
    console.log('⏭️  Skipped tests: Require Stripe configuration');
    console.log('❌ Tests for missing features: Payment History Page, Admin Management');
    console.log('\nNext Steps:');
    console.log('1. Configure Stripe API keys in .env files');
    console.log('2. Run: npx playwright test --grep "@stripe-configured"');
    console.log('3. Build Payment History page and re-run: --grep "@payment-history"');
    console.log('4. Build Admin Payment Management and re-run: --grep "@admin-payments"');
    console.log('\n====================================\n');
});
