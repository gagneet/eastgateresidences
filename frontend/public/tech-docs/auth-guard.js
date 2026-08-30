( function () {
    async function checkAuth() {
        try {
            document.body.style.opacity = '0.3';

            // Use NextAuth session endpoint — same pattern as user-guides/auth-guard.js
            const response = await fetch('/api/auth/session');
            const session = await response.json();

            if (!session || !session.user) {
                window.location.href = '/login?callbackUrl=' + encodeURIComponent(window.location.href);
                return;
            }

            const user = session.user.data || session.user;
            const role = user.role;

            if (!role) {
                window.location.href = '/login?callbackUrl=' + encodeURIComponent(window.location.href);
                return;
            }

            // Only super_admin may access any page in this folder
            if (role !== 'super_admin') {
                window.location.href = '/dashboard';
                return;
            }

            // Highlight the active nav link
            const path = window.location.pathname;
            const currentPage = path.split('/').pop() || 'index.html';
            const nav = document.querySelector('nav#sidebar');
            if (nav) {
                nav.querySelectorAll('a[data-page]').forEach(function (a) {
                    if (a.getAttribute('data-page') === currentPage) {
                        a.classList.add('active');
                    } else {
                        a.classList.remove('active');
                    }
                });
            }

            document.body.style.opacity = '1';

        } catch (error) {
            console.error('Auth check failed', error);
            window.location.href = '/login?callbackUrl=' + encodeURIComponent(window.location.href);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', checkAuth);
    } else {
        checkAuth();
    }
} )();
