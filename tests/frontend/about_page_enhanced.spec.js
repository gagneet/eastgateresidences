const {test, expect} = require('@playwright/test');

test('About Page has enhanced content and SEO metadata', async ({page}) => {
    await page.goto('/about');

    // Check SEO Title - Wait for the title to be updated by useEffect
    await page.waitForFunction(() => document.title === "About East Gate Residences | Denman Prospect ACT", {timeout: 10000});

    // Check SEO Description
    const metaDescription = await page.getAttribute('meta[name="description"]', 'content');
    expect(metaDescription).toContain('Learn about East Gate Residences in Denman Prospect');

    // Check stats are present - Use getByText with exact: true
    await expect(page.getByText('87', {exact: true})).toBeVisible();
    await expect(page.getByText('70', {exact: true})).toBeVisible();
    await expect(page.getByText('17', {exact: true})).toBeVisible();

    // Check Property Overview
    await expect(page.getByRole('heading', {name: 'Property Overview'})).toBeVisible();
    await expect(page.locator('text=Designed by the renowned Stewart Architects')).toBeVisible();

    // Check Key Features
    await expect(page.getByRole('heading', {name: 'Key Features & Inclusions'})).toBeVisible();
    await expect(page.locator('text=Heated Bathroom Floors')).toBeVisible();

    // Check Location & Lifestyle
    await expect(page.getByRole('heading', {name: 'Location & Lifestyle'})).toBeVisible();
    await expect(page.locator('text=Ridgeline Park & Playground')).toBeVisible();

    // Check External Resources
    await expect(page.getByRole('heading', {name: 'Project Resources'})).toBeVisible();
    const coreDevLink = page.locator('a[href="https://coredev.com.au/projects/east-gate/"]');
    await expect(coreDevLink).toBeVisible();

    // Final screenshot
    await page.screenshot({path: 'tests/screenshots/final_about_page_verified.png', fullPage: true});
});
