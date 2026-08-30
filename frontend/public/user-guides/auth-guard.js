( function () {
    async function checkAuth() {
        try {
            // Add a loading state to the body
            document.body.style.opacity = '0.5';

            const response = await fetch('/api/auth/session');
            const session = await response.json();

            if (!session || !session.user) {
                window.location.href = '/login?callbackUrl=' + encodeURIComponent(window.location.href);
                return;
            }

            const user = session.user.data || session.user;
            const role = user.role;
            const path = window.location.pathname;
            const currentPage = path.split('/').pop() || 'index.html';

            // Define permissions
            const permissions = {
                'role_admin.html': ['super_admin'],
                'role_strata_admin.html': ['super_admin', 'strata_admin'],
                'role_ec.html': ['super_admin', 'strata_admin', 'ec_member'],
                'role_owner.html': ['super_admin', 'strata_admin', 'ec_member', 'owner'],
                'role_tenant.html': ['super_admin', 'tenant'],
                'role_strata_manager.html': ['super_admin', 'strata_manager'],
                'role_reception.html': ['super_admin', 'admin_staff', 'reception'],
                'full_guide.html': ['super_admin', 'strata_admin', 'ec_member', 'owner', 'tenant', 'strata_manager', 'service_provider', 'admin_staff', 'reception'],
                'east_gate_inclusions.html': ['super_admin', 'strata_admin', 'ec_member', 'owner', 'tenant', 'strata_manager', 'service_provider', 'admin_staff', 'reception'],
                'units_plan.html': ['super_admin', 'strata_admin', 'ec_member', 'owner', 'strata_manager'],
                'owner_guide_units_plan.html': ['super_admin', 'strata_admin', 'ec_member', 'owner', 'strata_manager'],
                'index.html': ['super_admin', 'strata_admin', 'ec_member', 'owner', 'tenant', 'strata_manager', 'service_provider', 'admin_staff', 'reception']
            };

            if (permissions[ currentPage ] && !permissions[ currentPage ].includes(role)) {
                // Not authorized for this page
                console.warn('Unauthorized access to ' + currentPage + ' for role ' + role);
                window.location.href = 'index.html';
                return;
            }

            // Update Navigation
            const nav = document.querySelector('nav');
            if (nav) {
                const navLinks = [
                    {href: 'index.html', label: 'Home', roles: ['all']},
                    {href: 'full_guide.html', label: 'Resident Handbook', roles: ['all']},
                    {
                        href: 'role_owner.html',
                        label: 'Owner Handbook',
                        roles: ['super_admin', 'strata_admin', 'ec_member', 'owner']
                    },
                    {
                        href: 'role_ec.html',
                        label: 'EC Member Handbook',
                        roles: ['super_admin', 'strata_admin', 'ec_member']
                    },
                    {
                        href: 'role_strata_admin.html',
                        label: 'Strata Admin Handbook',
                        roles: ['super_admin', 'strata_admin']
                    },
                    {href: 'role_admin.html', label: 'Super Admin Handbook', roles: ['super_admin']},
                    {
                        href: 'role_reception.html',
                        label: 'Admin Staff Handbook',
                        roles: ['super_admin', 'admin_staff', 'reception']
                    },
                    {href: 'role_tenant.html', label: 'Tenant Handbook', roles: ['super_admin', 'tenant']},
                    {
                        href: 'role_strata_manager.html',
                        label: 'Strata Manager Handbook',
                        roles: ['super_admin', 'strata_manager']
                    },
                    {href: 'east_gate_inclusions.html', label: 'Unit Inclusions List', roles: ['all']},
                    {
                        href: 'units_plan.html',
                        label: 'Unit Plan (Viewer)',
                        roles: ['super_admin', 'strata_admin', 'ec_member', 'owner', 'strata_manager']
                    },
                    {
                        href: 'owner_guide_units_plan.html',
                        label: 'Unit Plan Guide',
                        roles: ['super_admin', 'strata_admin', 'ec_member', 'owner', 'strata_manager']
                    }
                ];

                let navHtml = '<h3>Navigation</h3>';
                navLinks.forEach(link => {
                    if (link.roles.includes('all') || link.roles.includes(role)) {
                        const activeClass = ( currentPage === link.href ) ? 'class="active"' : '';
                        navHtml += `<a href="${link.href}" ${activeClass}>${link.label}</a>`;
                    }
                });

                // Keep the screenshots card if it exists
                const shotCard = nav.querySelector('.callout.warn');
                nav.textContent = ''; // Clear safely
                const navHeader = document.createElement('h3');
                navHeader.textContent = 'Navigation';
                nav.appendChild(navHeader);

                navLinks.forEach(link => {
                    if (link.roles.includes('all') || link.roles.includes(role)) {
                        const anchor = document.createElement('a');
                        anchor.href = link.href;
                        anchor.textContent = link.label;
                        if (currentPage === link.href) {
                            anchor.className = 'active';
                        }
                        nav.appendChild(anchor);
                    }
                });

                if (shotCard) {
                    nav.appendChild(shotCard);
                }
            }

            // Show the content
            document.body.style.opacity = '1';

        } catch (error) {
            console.error('Auth check failed', error);
            // Redirect to login on authentication failure
            window.location.href = '/login?callbackUrl=' + encodeURIComponent(window.location.href);
        }
    }

    // Run early if possible, but wait for DOM for nav updates
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', checkAuth);
    } else {
        checkAuth();
    }
} )();
