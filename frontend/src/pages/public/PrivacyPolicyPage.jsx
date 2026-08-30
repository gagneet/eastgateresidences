import React, { useEffect, useState } from 'react';
import { Card, CardContent } from '../../components/ui/card';
import { Shield } from 'lucide-react';
import axios from 'axios';

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api`;
/**
 * @generated FunctionHeader
 * Function: PrivacyPolicyPage
 * Path: frontend/src/pages/public/PrivacyPolicyPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PrivacyPolicyPage = () => {
    const [content, setContent] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchContent
         * Path: frontend/src/pages/public/PrivacyPolicyPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchContent = async () => {
            try {
                const response = await axios.get(`${API_URL}/legal-pages/privacy-policy`);
                setContent(response.data.content);
            } catch (error) {
                console.error('Failed to fetch privacy policy:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchContent();
    }, []);

    const defaultContent = `
# Privacy Policy

**Last Updated:** ${new Date().toLocaleDateString('en-AU', {year: 'numeric', month: 'long', day: 'numeric'})}

## 1. Introduction

Welcome to East Gate Residences Owners Corporation. We are committed to protecting your privacy and handling your personal information in an open and transparent manner. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you use our strata management platform.

## 2. Information We Collect

### 2.1 Personal Information
We collect personal information that you provide to us, including but not limited to:
- Full name and contact details (email address, phone number)
- Unit number and property information
- Identification documents (for verification purposes)
- Financial information (for levy payments and financial management)
- Profile photographs and preferences

### 2.2 Automatically Collected Information
When you access our platform, we automatically collect:
- IP address and device information
- Browser type and version
- Usage patterns and preferences
- Login times and activity logs

### 2.3 Communications
We collect information when you:
- Communicate through our messaging system
- Post in community forums or chat rooms
- Submit maintenance requests or defect reports
- Participate in surveys or feedback forms

## 3. How We Use Your Information

We use the collected information for the following purposes:
- **Administration:** Managing your account and providing access to services
- **Communication:** Sending announcements, meeting notices, and important updates
- **Financial Management:** Processing levy payments and maintaining financial records
- **Maintenance:** Managing maintenance requests and building defects
- **Community Services:** Facilitating communication between residents and the Executive Committee
- **Legal Compliance:** Meeting our obligations under strata legislation
- **Security:** Protecting against unauthorized access and fraudulent activities

## 4. Information Sharing and Disclosure

### 4.1 Within the Corporation
Your information may be shared with:
- Executive Committee members (for administrative purposes)
- Building manager and strata management company
- Other residents (as appropriate for community functions)

### 4.2 Third Parties
We may share your information with:
- Service providers (payment processors, IT support, maintenance contractors)
- Legal and financial advisors
- Government authorities (when required by law)
- Emergency services (in emergency situations)

### 4.3 We Do Not Sell Your Information
We will never sell, rent, or trade your personal information to third parties for marketing purposes.

## 5. Data Security

We implement appropriate technical and organizational measures to protect your personal information:
- Secure socket layer (SSL) encryption for data transmission
- Role-based access controls
- Regular security audits and updates
- Secure backup procedures
- Password protection and authentication systems

## 6. Your Rights

Under Australian Privacy Law, you have the right to:
- **Access:** Request access to your personal information
- **Correction:** Request correction of inaccurate information
- **Deletion:** Request deletion of your information (subject to legal requirements)
- **Restriction:** Request restriction of processing
- **Objection:** Object to certain uses of your information
- **Portability:** Request transfer of your information to another service

## 7. Data Retention

We retain your personal information for as long as:
- You are a member of the Owners Corporation
- Required by strata legislation (typically 7 years for financial records)
- Necessary for legal or regulatory purposes

## 8. Cookies and Tracking Technologies

Our platform uses cookies and similar technologies to:
- Remember your login preferences
- Analyze usage patterns
- Improve platform functionality
- Enhance user experience

You can control cookie settings through your browser preferences.

## 9. Children's Privacy

Our services are not intended for individuals under 18 years of age. We do not knowingly collect information from children.

## 10. Changes to This Policy

We may update this Privacy Policy from time to time. We will notify you of any significant changes by:
- Posting the updated policy on our platform
- Sending an email notification to registered users
- Displaying a notice on the dashboard

## 11. Third-Party Links

Our platform may contain links to third-party websites. We are not responsible for the privacy practices of these external sites.

## 12. Contact Information

For privacy-related inquiries or to exercise your rights, please contact:

**Privacy Officer**  
East Gate Residences Owners Corporation  
Email: privacy@eastgateresidences.com.au  
Address: Denman Prospect, ACT, Australia

## 13. Complaints

If you believe your privacy has been breached, please contact us immediately. If you are not satisfied with our response, you may lodge a complaint with:

**Office of the Australian Information Commissioner (OAIC)**  
Website: www.oaic.gov.au  
Phone: 1300 363 992

## 14. Consent

By using our platform, you consent to the collection and use of your information as described in this Privacy Policy.

---

This Privacy Policy is compliant with the Australian Privacy Act 1988 and the Australian Privacy Principles (APPs).
`;

    if (loading) {
        return (
            <div className="max-w-4xl mx-auto py-12 px-4">
                <div className="animate-pulse space-y-4">
                    <div className="h-8 bg-muted rounded w-1/3"/>
                    <div className="h-4 bg-muted rounded w-full"/>
                    <div className="h-4 bg-muted rounded w-full"/>
                    <div className="h-4 bg-muted rounded w-2/3"/>
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-4xl mx-auto py-12 px-4" data-testid="privacy-policy-page">
            <div className="mb-8 flex items-center gap-4">
                <div className="p-3 rounded-full bg-primary/10">
                    <Shield className="h-8 w-8 text-primary"/>
                </div>
                <div>
                    <h1 className="text-3xl font-bold">Privacy Policy</h1>
                    <p className="text-muted-foreground">How we protect and handle your personal information</p>
                </div>
            </div>

            <Card className="card-dashboard">
                <CardContent className="p-8 prose prose-slate dark:prose-invert max-w-none">
                    {/* Note: Content is rendered with basic HTML escaping. Admin-edited content should avoid script tags. */}
                    <div
                        dangerouslySetInnerHTML={{
                            __html: ( content || defaultContent )
                                .split('\n')
                                .map(line => {
                                    // Basic HTML escape for user content
                                    /**
                                     * @generated FunctionHeader
                                     * Function: escape
                                     * Path: frontend/src/pages/public/PrivacyPolicyPage.jsx
                                     *
                                     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
                                     */
                                    const escape = (text) => text
                                        .replace(/&/g, '&amp;')
                                        .replace(/</g, '&lt;')
                                        .replace(/>/g, '&gt;')
                                        .replace(/"/g, '&quot;')
                                        .replace(/'/g, '&#039;');

                                    if (line.startsWith('# ')) {
                                        return `<h1 class="text-3xl font-bold mt-8 mb-4">${escape(line.substring(2))}</h1>`;
                                    } else if (line.startsWith('## ')) {
                                        return `<h2 class="text-2xl font-bold mt-6 mb-3">${escape(line.substring(3))}</h2>`;
                                    } else if (line.startsWith('### ')) {
                                        return `<h3 class="text-xl font-semibold mt-4 mb-2">${escape(line.substring(4))}</h3>`;
                                    } else if (line.startsWith('**') && line.endsWith('**')) {
                                        return `<p class="font-bold">${escape(line.substring(2, line.length - 2))}</p>`;
                                    } else if (line.startsWith('- ')) {
                                        return `<li class="ml-6">${escape(line.substring(2))}</li>`;
                                    } else if (line.trim() === '---') {
                                        return '<hr class="my-8 border-border" />';
                                    } else if (line.trim() === '') {
                                        return '<br />';
                                    } else {
                                        return `<p class="mb-3">${escape(line)}</p>`;
                                    }
                                })
                                .join('')
                        }}
                    />
                </CardContent>
            </Card>
        </div>
    );
};

export default PrivacyPolicyPage;
