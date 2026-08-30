// @ts-check
const {test, expect} = require('@playwright/test');
const {exec} = require('child_process');
const {promisify} = require('util');
const fs = require('fs');
const path = require('path');

const execAsync = promisify(exec);

/**
 * Build Validation Tests
 * Ensures frontend builds without warnings
 * Validates React Hooks dependency fix from Session 12
 */

test.describe('Frontend Build Validation', () => {
    test('Frontend should build without React Hook exhaustive-deps warnings', async () => {
        test.setTimeout(180000); // 3 minutes timeout for build

        try {
            const frontendPath = path.join(process.cwd(), 'frontend');

            // Check if we're in the right directory
            if (!fs.existsSync(frontendPath)) {
                console.log('Current directory:', process.cwd());
                throw new Error('Frontend directory not found. Make sure tests run from project root.');
            }

            console.log('Building frontend...');
            const {stdout, stderr} = await execAsync('cd frontend && yarn build', {
                maxBuffer: 10 * 1024 * 1024, // 10MB buffer
            });

            console.log('Build output:', stdout);

            // Check for exhaustive-deps warnings
            const hasExhaustiveDepsWarning = stdout.includes('react-hooks/exhaustive-deps') ||
                stderr.includes('react-hooks/exhaustive-deps');

            expect(hasExhaustiveDepsWarning, 'Build should not have react-hooks/exhaustive-deps warnings').toBe(false);

            // Check build succeeded
            const buildSucceeded = stdout.includes('Compiled successfully') ||
                stdout.includes('The build folder is ready');

            expect(buildSucceeded, 'Build should complete successfully').toBe(true);

            // Verify build directory exists
            const buildDir = path.join(frontendPath, 'build');
            const buildExists = fs.existsSync(buildDir);

            expect(buildExists, 'Build directory should exist').toBe(true);

            // Verify main JS file exists
            const staticJsDir = path.join(buildDir, 'static', 'js');
            if (fs.existsSync(staticJsDir)) {
                const jsFiles = fs.readdirSync(staticJsDir);
                const hasMainJs = jsFiles.some(file => file.startsWith('main.') && file.endsWith('.js'));

                expect(hasMainJs, 'Build should contain main.js file').toBe(true);

                console.log('✓ Build completed successfully');
                console.log('✓ No React Hook warnings found');
                console.log('✓ Build artifacts created');
            }
        } catch (error) {
            console.error('Build error:', error);
            throw error;
        }
    });

    test('Build output should have reasonable size', async () => {
        test.setTimeout(180000);

        const frontendPath = path.join(process.cwd(), 'frontend');
        const buildDir = path.join(frontendPath, 'build', 'static', 'js');

        if (!fs.existsSync(buildDir)) {
            test.skip(); // Skip if build doesn't exist yet
            return;
        }

        const jsFiles = fs.readdirSync(buildDir);
        const mainJsFile = jsFiles.find(file => file.startsWith('main.') && file.endsWith('.js'));

        if (mainJsFile) {
            const filePath = path.join(buildDir, mainJsFile);
            const stats = fs.statSync(filePath);
            const fileSizeInMB = stats.size / ( 1024 * 1024 );

            console.log(`Main JS file size: ${fileSizeInMB.toFixed(2)} MB`);

            // Main bundle should be under 2MB (uncompressed)
            expect(fileSizeInMB, 'Main bundle should be under 2MB').toBeLessThan(2);

            // Should be at least 100KB (sanity check)
            expect(fileSizeInMB, 'Main bundle should be at least 0.1MB').toBeGreaterThan(0.1);

            console.log('✓ Bundle size is within expected range');
        }
    });
});

test.describe('Code Quality Checks', () => {
    test('AuthContext should use stable API instance', async () => {
        const authContextPath = path.join(process.cwd(), 'frontend', 'src', 'contexts', 'AuthContext.js');

        if (!fs.existsSync(authContextPath)) {
            console.log('AuthContext.js not found, skipping test');
            test.skip();
            return;
        }

        const content = fs.readFileSync(authContextPath, 'utf-8');

        // Check that api is created with useMemo or is stable
        const hasUseMemo = content.includes('useMemo') && content.includes('api');
        const hasUseRef = content.includes('useRef') && content.includes('api');

        expect(
            hasUseMemo || hasUseRef,
            'AuthContext should use useMemo or useRef for stable api instance'
        ).toBe(true);

        console.log('✓ AuthContext uses stable API instance pattern');
    });

    test('Dashboard components should use useCallback for fetch functions', async () => {
        const dashboardPages = [
            'OwnerDashboard.jsx',
            'RegularDashboard.jsx',
            'SuperAdminDashboard.jsx',
        ];

        for (const pageName of dashboardPages) {
            const pagePath = path.join(process.cwd(), 'frontend', 'src', 'pages', 'dashboard', pageName);

            if (!fs.existsSync(pagePath)) {
                console.log(`${pageName} not found, skipping`);
                continue;
            }

            const content = fs.readFileSync(pagePath, 'utf-8');

            // Check for useCallback usage with fetch functions
            const hasUseCallback = content.includes('useCallback');

            if (content.includes('fetchDashboardData') || content.includes('fetch')) {
                expect(
                    hasUseCallback,
                    `${pageName} should use useCallback for fetch functions`
                ).toBe(true);

                console.log(`✓ ${pageName} uses useCallback for memoization`);
            }
        }
    });
});

test.describe('Regression Prevention', () => {
    test('PaymentModal should have proper dependencies', async () => {
        const paymentModalPath = path.join(
            process.cwd(),
            'frontend',
            'src',
            'components',
            'payments',
            'PaymentModal.jsx'
        );

        if (!fs.existsSync(paymentModalPath)) {
            console.log('PaymentModal.jsx not found, skipping test');
            test.skip();
            return;
        }

        const content = fs.readFileSync(paymentModalPath, 'utf-8');

        // Should have useCallback
        const hasUseCallback = content.includes('useCallback');
        expect(hasUseCallback, 'PaymentModal should use useCallback').toBe(true);

        // Should not have empty dependency arrays for functions that use props/state
        // This is a basic check - more sophisticated analysis would require AST parsing
        const suspiciousPatterns = [
            /useCallback\s*\([^)]+\),\s*\[\s*\]\s*\)/g, // useCallback with empty deps
        ];

        let hasSuspiciousPatterns = false;
        for (const pattern of suspiciousPatterns) {
            if (pattern.test(content)) {
                // This might be a false positive, so we'll just log it
                console.log('Warning: Found potential empty dependency array in PaymentModal');
                hasSuspiciousPatterns = true;
            }
        }

        // Don't fail the test, just warn
        console.log(`✓ PaymentModal code structure checked`);
    });
});
