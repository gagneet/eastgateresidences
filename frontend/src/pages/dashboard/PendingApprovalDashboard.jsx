import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '../../components/ui/alert';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { AlertCircle, ArrowRight, CheckCircle2, Clock, HelpCircle, Home, Info, Mail, Phone, User } from 'lucide-react';
/**
 * PendingApprovalDashboard Component
 *
 * Shown to users whose accounts are pending approval (is_approved=false).
 * Implements UX best practices for pending approval states:
 * - Clear status communication
 * - Set expectations with timeline
 * - Provide contact information
 * - Show limited safe features (profile viewing)
 * - Friendly, reassuring tone
 *
 * Best Practices Sources:
 * - Dashboard UX Best Practices 2026: https://www.designrush.com/agency/ui-ux-design/dashboard/trends/dashboard-ux
 * - SaaS Verification UI: https://www.saasframe.io/categories/verification
 * - Complex Approvals Design: https://www.uxpin.com/studio/blog/complex-approvals-app-design/
 */
const PendingApprovalDashboard = () => {
    const {user, logout} = useAuth();
    // Determine approval message based on role
    /**
     * @generated FunctionHeader
     * Function: getApprovalMessage
     * Path: frontend/src/pages/dashboard/PendingApprovalDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getApprovalMessage = () => {
        switch (user?.role) {
            case 'owner':
                return {
                    title: 'Account Pending Ownership Verification',
                    description: 'Our team is verifying your ownership of the unit. This typically takes 1-2 business days.',
                    icon: Home,
                    color: 'text-blue-600',
                    bgColor: 'bg-blue-50',
                    borderColor: 'border-blue-200'
                };
            case 'tenant':
                return {
                    title: 'Account Pending Approval',
                    description: 'Your account is being reviewed by the building management. You will be notified once approved.',
                    icon: Clock,
                    color: 'text-orange-600',
                    bgColor: 'bg-orange-50',
                    borderColor: 'border-orange-200'
                };
            default:
                return {
                    title: 'Account Under Review',
                    description: 'Your account is being reviewed by the administration team.',
                    icon: Clock,
                    color: 'text-gray-600',
                    bgColor: 'bg-gray-50',
                    borderColor: 'border-gray-200'
                };
        }
    };

    const approvalInfo = getApprovalMessage();
    const ApprovalIcon = approvalInfo.icon;

    return (
        <div className="min-h-screen bg-gradient-to-br from-muted/30 via-background to-muted/20">
            <div className="container max-w-4xl mx-auto px-4 py-12 space-y-6">

                {/* Main Status Banner - Prominent and Clear */}
                <Alert className={`${approvalInfo.bgColor} ${approvalInfo.borderColor} border-2 shadow-lg`}>
                    <div className="flex items-start gap-4">
                        <div className={`p-3 rounded-full ${approvalInfo.bgColor} ${approvalInfo.borderColor} border`}>
                            <ApprovalIcon className={`h-8 w-8 ${approvalInfo.color}`}/>
                        </div>
                        <div className="flex-1">
                            <AlertTitle className={`text-2xl font-bold ${approvalInfo.color} mb-2`}>
                                {approvalInfo.title}
                            </AlertTitle>
                            <AlertDescription className="text-base text-foreground/80 leading-relaxed">
                                {approvalInfo.description}
                            </AlertDescription>
                        </div>
                        <Badge variant="outline"
                               className={`${approvalInfo.color} ${approvalInfo.borderColor} border-2 px-3 py-1 text-sm whitespace-nowrap`}>
                            Pending Review
                        </Badge>
                    </div>
                </Alert>

                {/* What Happens Next Section */}
                <Card className="border-2 shadow-md">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <Info className="h-5 w-5 text-primary"/>
                            <CardTitle>What Happens Next?</CardTitle>
                        </div>
                        <CardDescription>Here's what you can expect during the approval process</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4">
                            {/* Step 1 */}
                            <div className="flex items-start gap-3 p-4 rounded-lg bg-muted/50 border">
                                <div
                                    className="flex items-center justify-center w-8 h-8 rounded-full bg-primary text-primary-foreground font-semibold text-sm flex-shrink-0">
                                    1
                                </div>
                                <div className="flex-1">
                                    <h4 className="font-semibold mb-1">Verification in Progress</h4>
                                    <p className="text-sm text-muted-foreground">
                                        {user?.role === 'owner'
                                            ? 'Our team is verifying your ownership documents and unit details.'
                                            : 'The building administrator is reviewing your registration details.'}
                                    </p>
                                </div>
                                <Clock className="h-5 w-5 text-muted-foreground animate-pulse"/>
                            </div>

                            {/* Step 2 */}
                            <div className="flex items-start gap-3 p-4 rounded-lg bg-muted/30 border border-dashed">
                                <div
                                    className="flex items-center justify-center w-8 h-8 rounded-full bg-muted text-muted-foreground font-semibold text-sm flex-shrink-0">
                                    2
                                </div>
                                <div className="flex-1">
                                    <h4 className="font-semibold mb-1">Email Notification</h4>
                                    <p className="text-sm text-muted-foreground">
                                        You'll receive an email at <strong>{user?.email}</strong> once your account is
                                        approved.
                                    </p>
                                </div>
                                <Mail className="h-5 w-5 text-muted-foreground"/>
                            </div>

                            {/* Step 3 */}
                            <div className="flex items-start gap-3 p-4 rounded-lg bg-muted/30 border border-dashed">
                                <div
                                    className="flex items-center justify-center w-8 h-8 rounded-full bg-muted text-muted-foreground font-semibold text-sm flex-shrink-0">
                                    3
                                </div>
                                <div className="flex-1">
                                    <h4 className="font-semibold mb-1">Full Access Granted</h4>
                                    <p className="text-sm text-muted-foreground">
                                        Log back in to access all features: documents, announcements, marketplace, and
                                        more.
                                    </p>
                                </div>
                                <CheckCircle2 className="h-5 w-5 text-muted-foreground"/>
                            </div>
                        </div>

                        {/* Timeline Estimate */}
                        <div className="mt-6 p-4 rounded-lg bg-blue-50 border border-blue-200">
                            <div className="flex items-center gap-2 text-blue-800">
                                <Clock className="h-4 w-4"/>
                                <p className="text-sm font-semibold">
                                    Estimated Approval Time:
                                    <span className="ml-1">
                    {user?.role === 'owner' ? '1-2 business days' : '24-48 hours'}
                  </span>
                                </p>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Your Account Details */}
                <Card className="border-2 shadow-md">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <User className="h-5 w-5 text-primary"/>
                            <CardTitle>Your Account Details</CardTitle>
                        </div>
                        <CardDescription>Information submitted during registration</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-muted-foreground">Full Name</label>
                                <p className="text-base font-semibold">{user?.full_name || 'N/A'}</p>
                            </div>
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-muted-foreground">Email Address</label>
                                <p className="text-base font-semibold flex items-center gap-2">
                                    <Mail className="h-4 w-4 text-muted-foreground"/>
                                    {user?.email || 'N/A'}
                                </p>
                            </div>
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-muted-foreground">Unit Number</label>
                                <p className="text-base font-semibold flex items-center gap-2">
                                    <Home className="h-4 w-4 text-muted-foreground"/>
                                    {user?.unit_number || 'Not assigned'}
                                </p>
                            </div>
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-muted-foreground">Account Type</label>
                                <Badge variant="secondary" className="text-sm capitalize">
                                    {user?.role || 'N/A'}
                                </Badge>
                            </div>
                            {user?.phone && (
                                <div className="space-y-1">
                                    <label className="text-sm font-medium text-muted-foreground">Phone Number</label>
                                    <p className="text-base font-semibold flex items-center gap-2">
                                        <Phone className="h-4 w-4 text-muted-foreground"/>
                                        {user.phone}
                                    </p>
                                </div>
                            )}
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-muted-foreground">Registration Date</label>
                                <p className="text-base font-semibold">
                                    {user?.created_at ? new Date(user.created_at).toLocaleDateString('en-AU', {
                                        year: 'numeric',
                                        month: 'long',
                                        day: 'numeric'
                                    }) : 'N/A'}
                                </p>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Help & Contact Section */}
                <Card className="border-2 shadow-md">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <HelpCircle className="h-5 w-5 text-primary"/>
                            <CardTitle>Need Help?</CardTitle>
                        </div>
                        <CardDescription>Get in touch with our team if you have questions</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Alert>
                            <AlertCircle className="h-4 w-4"/>
                            <AlertTitle>Questions about your approval?</AlertTitle>
                            <AlertDescription>
                                Contact the building management team for status updates or if you have concerns about
                                your application.
                            </AlertDescription>
                        </Alert>

                        <div className="grid gap-3 md:grid-cols-2">
                            <a
                                href="mailto:admin@eastgate.com"
                                className="flex items-center gap-3 p-4 rounded-lg border-2 hover:border-primary hover:bg-muted/50 transition-colors group"
                            >
                                <div
                                    className="p-2 rounded-full bg-primary/10 group-hover:bg-primary/20 transition-colors">
                                    <Mail className="h-5 w-5 text-primary"/>
                                </div>
                                <div className="flex-1">
                                    <p className="font-semibold text-sm">Email Support</p>
                                    <p className="text-xs text-muted-foreground">admin@eastgate.com</p>
                                </div>
                                <ArrowRight
                                    className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors"/>
                            </a>

                            <a
                                href="tel:0261234567"
                                className="flex items-center gap-3 p-4 rounded-lg border-2 hover:border-primary hover:bg-muted/50 transition-colors group"
                            >
                                <div
                                    className="p-2 rounded-full bg-primary/10 group-hover:bg-primary/20 transition-colors">
                                    <Phone className="h-5 w-5 text-primary"/>
                                </div>
                                <div className="flex-1">
                                    <p className="font-semibold text-sm">Phone Support</p>
                                    <p className="text-xs text-muted-foreground">(02) 6123 4567</p>
                                </div>
                                <ArrowRight
                                    className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors"/>
                            </a>
                        </div>

                        {/* Office Hours */}
                        <div className="p-4 rounded-lg bg-muted/50 border">
                            <p className="text-sm font-semibold mb-2">Office Hours</p>
                            <div className="text-xs text-muted-foreground space-y-1">
                                <p>Monday - Friday: 9:00 AM - 5:00 PM</p>
                                <p>Weekend: Closed</p>
                                <p className="text-xs italic mt-2">Response time: Within 24 hours on business days</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Action Buttons */}
                <div className="flex flex-col sm:flex-row gap-3 justify-center items-center pt-4">
                    <Button
                        variant="outline"
                        size="lg"
                        onClick={logout}
                        className="w-full sm:w-auto"
                    >
                        Sign Out
                    </Button>
                    <Button
                        variant="default"
                        size="lg"
                        onClick={() => window.location.reload()}
                        className="w-full sm:w-auto"
                    >
                        <CheckCircle2 className="mr-2 h-4 w-4"/>
                        Check Approval Status
                    </Button>
                </div>

                {/* Footer Note */}
                <div className="text-center text-sm text-muted-foreground pt-4">
                    <p>This page will automatically update once your account is approved.</p>
                </div>
            </div>
        </div>
    );
};

export default PendingApprovalDashboard;
