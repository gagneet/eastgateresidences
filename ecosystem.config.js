/**
 * PM2 Ecosystem Configuration for Strata Management Platform
 *
 * This configuration manages both the FastAPI backend and React frontend
 *
 * Usage:
 *   pm2 start ecosystem.config.js                    # Start all services
 *   pm2 start ecosystem.config.js --only backend     # Start backend only
 *   pm2 start ecosystem.config.js --only frontend    # Start frontend only
 *   pm2 start ecosystem.config.js --env production   # Start in production mode
 *
 * Management:
 *   pm2 status                      # View status
 *   pm2 logs eastgate               # View all logs
 *   pm2 logs eastgate-backend       # View backend logs
 *   pm2 logs eastgate-frontend      # View frontend logs
 *   pm2 monit                       # Monitor resources
 *   pm2 restart eastgate-backend    # Restart backend
 *   pm2 restart eastgate-frontend   # Restart frontend
 *   pm2 restart all                 # Restart all services
 *   pm2 stop all                    # Stop all services
 *   pm2 delete all                  # Remove from PM2
 *   pm2 save                        # Save PM2 configuration
 *   pm2 startup                     # Setup auto-start on boot
 */

module.exports = {
    apps: [
        {
            // FastAPI Backend
            name: 'eastgate-backend',
            script: '/home/gagneet/strata-management/backend/venv/bin/python3',
            args: '-m uvicorn server:app --host 0.0.0.0 --port 8000',
            cwd: '/home/gagneet/strata-management/backend',
            interpreter: 'none',

            // Process management
            instances: 1,
            exec_mode: 'fork',
            autorestart: true,
            watch: false,
            max_memory_restart: '512M',

            // Environment variables
            env: {
                PYTHONUNBUFFERED: '1',
                PATH: '/home/gagneet/strata-management/backend/venv/bin:' + process.env.PATH,
            },

            // Logging
            error_file: '/home/gagneet/strata-management/logs/backend-error.log',
            out_file: '/home/gagneet/strata-management/logs/backend-out.log',
            log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
            merge_logs: true,

            // Restart configuration
            max_restarts: 10,
            min_uptime: '10s',
            restart_delay: 4000,

            // Kill timeout
            kill_timeout: 5000,
        },

        {
            // React Frontend (using serve to host the build)
            name: 'eastgate-frontend',
            script: 'npx',
            args: 'serve -s build -l 3016',
            cwd: '/home/gagneet/strata-management/frontend',

            // Process management
            instances: 1,
            exec_mode: 'fork',
            autorestart: true,
            watch: false,
            max_memory_restart: '256M',

            // Environment variables
            env: {
                NODE_ENV: 'production',
            },

            // Logging
            error_file: '/home/gagneet/strata-management/logs/frontend-error.log',
            out_file: '/home/gagneet/strata-management/logs/frontend-out.log',
            log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
            merge_logs: true,

            // Restart configuration
            max_restarts: 10,
            min_uptime: '10s',
            restart_delay: 4000,

            // Kill timeout
            kill_timeout: 3000,
        }
    ]
};
