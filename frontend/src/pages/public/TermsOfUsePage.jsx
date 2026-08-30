import React, { useEffect, useState } from 'react';
import { Card, CardContent } from '../../components/ui/card';
import { FileText } from 'lucide-react';
import axios from 'axios';

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api`;
/**
 * @generated FunctionHeader
 * Function: TermsOfUsePage
 * Path: frontend/src/pages/public/TermsOfUsePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TermsOfUsePage = () => {
    const [content, setContent] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchContent
         * Path: frontend/src/pages/public/TermsOfUsePage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchContent = async () => {
            try {
                const response = await axios.get(`${API_URL}/legal-pages/terms-of-use`);
                setContent(response.data.content);
            } catch (error) {
                console.error('Failed to fetch terms of use:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchContent();
    }, []);

    const defaultContent = `
# Terms of Use

**Last Updated:** ${new Date().toLocaleDateString('en-AU', {year: 'numeric', month: 'long', day: 'numeric'})}

---

## Community Guidelines

In order to ensure the Owners Portal remains friendly, informative and welcoming for all clients, we monitor feeds on a regular basis.

Strata encourages discussion and debate through the Portal, however, please note that spam will be removed. Spam includes unrelated information, excessively posting large quantities of questions after an answer has been provided, defamatory posts and comments, business promotion or promotion of another website.

### In using Owners Portal, you agree:

- **To treat all users with respect and courtesy** - We are a community, and mutual respect is essential
- **Not to use this platform to defame, insult, abuse, harass, threaten or attack anyone** - Personal attacks have no place here
- **Not to engage in conversation that is offensive, obscene or vulgar** - Keep discussions professional and appropriate
- **Not to engage in conduct that is fraudulent, deceptive or misleading** - This includes not impersonating or falsely representing any other person or organisation
- **Not to engage in conduct that advertises, offers or promotes anything of a commercial nature** - No spam or commercial solicitation
- **Not to deliberately disrupt discussions (e.g. trolling)** - Constructive criticism is welcome, trolling is not
- **Not to post irrelevant or excessively long material** - Keep posts on-topic and concise

### Content Moderation

Whilst we deem it a major step, when appropriate we will take action by removing, deleting, hiding or blocking content when it is in breach of the above policies.

We thank you in advance for your compliance to this policy and look forward to you using the platform as it was designed - to foster positive and insightful conversation between clients and Strata staff and EC.

---

## 1. Acceptance of Terms

By accessing and using the East Gate Residences Owners Corporation management platform ("Platform"), you agree to be bound by these Terms of Use ("Terms"). If you do not agree to these Terms, please do not use the Platform.

## 2. Definitions

- **"Owners Corporation"** refers to East Gate Residences Owners Corporation
- **"Platform"** refers to the web-based strata management system
- **"User"** refers to any person accessing the Platform
- **"Resident"** refers to owners, tenants, and occupants of units
- **"Executive Committee" or "EC"** refers to the elected committee managing the Owners Corporation
- **"Content"** refers to all information, data, text, images, and materials on the Platform

## 3. User Accounts and Registration

### 3.1 Account Creation
- Users must register with accurate and complete information
- Each user is responsible for maintaining the confidentiality of their account credentials
- Users must be at least 18 years of age to create an account
- Only one account per user is permitted

### 3.2 Account Verification
- New accounts require verification and approval by the Executive Committee or Administrator
- The Owners Corporation reserves the right to deny or revoke access at any time
- Unit ownership or tenancy must be verified before full access is granted

### 3.3 Account Security
- Users are responsible for all activities under their account
- Users must notify us immediately of any unauthorized access
- Sharing account credentials is strictly prohibited

## 4. User Roles and Access

### 4.1 Role Types
The Platform operates with role-based access control:
- **Super Administrator:** Full system access and management
- **Chairman:** Executive Committee Chair with enhanced permissions
- **EC Member:** Executive Committee member with administrative functions
- **Owner:** Unit owners with standard access
- **Tenant:** Tenants with limited access appropriate to their status
- **Service Provider:** External contractors with schedule-only access
- **Guest:** Limited view-only access pending approval

### 4.2 Permission Restrictions
- Users may only access information appropriate to their role
- Attempts to access unauthorized information may result in account suspension
- Role changes require approval from the Executive Committee

## 5. Acceptable Use Policy

### 5.1 Permitted Uses
Users may use the Platform to:
- Access building information and documents
- Communicate with residents and the Executive Committee
- Submit maintenance requests and view schedules
- Participate in community discussions and forums
- Access financial information (where authorized)
- Book amenities and facilities
- View and respond to announcements

### 5.2 Prohibited Uses
Users must not:
- Use the Platform for any illegal purpose
- Harass, abuse, or threaten other users
- Post offensive, defamatory, or discriminatory content
- Share false or misleading information
- Attempt to gain unauthorized access to restricted areas
- Interfere with the Platform's operation or security
- Use the Platform for commercial solicitation without permission
- Violate the privacy of other residents
- Upload malicious software or viruses
- Impersonate another person or entity

## 6. Content and Intellectual Property

### 6.1 User Content
- Users retain ownership of content they post
- By posting content, users grant the Owners Corporation a license to use, display, and distribute that content on the Platform
- Users are responsible for ensuring they have rights to any content they post
- The Owners Corporation may remove any content that violates these Terms

### 6.2 Platform Content
- All Platform software, design, and materials are owned by or licensed to the Owners Corporation
- Users may not copy, modify, or distribute Platform content without permission
- Trademarks and logos remain the property of their respective owners

## 7. Community Communications

### 7.1 Forum and Chat Guidelines
- Be respectful and courteous to all community members
- Keep discussions relevant to building and community matters
- Respect privacy and confidentiality
- Report inappropriate behavior to administrators

### 7.2 Announcements and Notices
- Official announcements from the Executive Committee are binding
- Users are responsible for reading announcements in a timely manner
- Legal notices posted on the Platform constitute valid notification

## 8. Financial Transactions

### 8.1 Levy Payments
- Users are responsible for paying levies according to the schedule
- The Platform facilitates but does not guarantee payment processing
- Standard levy payment terms and conditions apply

### 8.2 Marketplace Transactions
- The Platform facilitates resident-to-resident transactions
- The Owners Corporation is not party to these transactions
- Users engage in marketplace activities at their own risk

## 9. Meetings and Voting

### 9.1 AGM and General Meetings
- Meeting notices posted on the Platform constitute valid notification
- Electronic voting may be conducted through the Platform
- Voting procedures follow the Strata Schemes Management Act 2015 (NSW) and Unit Titles (Management and Administration) Act 2011 (ACT)

### 9.2 Electronic Participation
- Virtual meeting attendance may be permitted
- Electronic votes are binding and equivalent to physical votes
- Technical issues do not invalidate properly conducted votes

## 10. Maintenance and Service Requests

### 10.1 Request Submission
- Users may submit maintenance and defect reports through the Platform
- Requests will be reviewed and prioritized by the Executive Committee
- Emergency requests should also be reported by phone

### 10.2 Response Times
- The Owners Corporation will respond to requests within reasonable timeframes
- Emergency requests receive priority handling
- Non-urgent requests are handled according to maintenance schedules

## 11. Data and Privacy

### 11.1 Data Protection
- Personal information is handled according to our Privacy Policy
- Users consent to data processing as described in the Privacy Policy
- The Owners Corporation implements security measures to protect user data

### 11.2 Data Retention
- User data is retained according to legal requirements
- Financial records are maintained for at least 7 years
- Users may request data deletion subject to legal obligations

## 12. Liability and Disclaimers

### 12.1 Platform Availability
- The Platform is provided "as is" without warranties
- We do not guarantee uninterrupted or error-free operation
- Scheduled maintenance may temporarily limit access

### 12.2 Limitation of Liability
- The Owners Corporation is not liable for indirect or consequential damages
- Users are responsible for maintaining backup copies of their important data
- The Owners Corporation's liability is limited to the maximum extent permitted by law

### 12.3 Third-Party Services
- The Platform may integrate with third-party services
- We are not responsible for third-party service failures or issues
- Third-party services have their own terms and conditions

## 13. Indemnification

Users agree to indemnify and hold harmless the Owners Corporation, Executive Committee members, and service providers from any claims, damages, or expenses arising from:
- Violation of these Terms
- Misuse of the Platform
- Infringement of third-party rights
- User-generated content

## 14. Modifications to Terms

### 14.1 Right to Modify
- We reserve the right to modify these Terms at any time
- Users will be notified of significant changes
- Continued use after modifications constitutes acceptance

### 14.2 Review Obligation
- Users are responsible for reviewing Terms periodically
- Current Terms are always available on the Platform

## 15. Termination

### 15.1 User Termination
- Users may close their accounts at any time
- Certain obligations survive account closure

### 15.2 Platform Termination
- We may suspend or terminate accounts for Terms violations
- We may discontinue the Platform with reasonable notice
- Data export options will be provided where practicable

## 16. Governing Law

### 16.1 Jurisdiction
These Terms are governed by the laws of the Australian Capital Territory, Australia.

### 16.2 Dispute Resolution
- Disputes should first be addressed with the Executive Committee
- Mediation is encouraged before formal legal action
- Courts of the Australian Capital Territory have exclusive jurisdiction

## 17. Strata Legislation Compliance

This Platform operates in compliance with:
- Unit Titles (Management and Administration) Act 2011 (ACT)
- Unit Titles (Management and Administration) Regulation 2011 (ACT)
- Relevant Australian Consumer Law
- Australian Privacy Principles

## 18. Contact Information

For questions about these Terms, contact:

**Executive Committee**  
East Gate Residences Owners Corporation  
Email: info@eastgateresidences.com.au  
Address: Denman Prospect, ACT, Australia

## 19. Severability

If any provision of these Terms is found to be invalid or unenforceable, the remaining provisions remain in full force and effect.

## 20. Entire Agreement

These Terms, together with the Privacy Policy and any other referenced policies, constitute the entire agreement between users and the Owners Corporation regarding use of the Platform.

---

**Acknowledgment:** By clicking "I Accept" during registration or by continuing to use the Platform, you acknowledge that you have read, understood, and agree to be bound by these Terms of Use.
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
        <div className="max-w-4xl mx-auto py-12 px-4" data-testid="terms-of-use-page">
            <div className="mb-8 flex items-center gap-4">
                <div className="p-3 rounded-full bg-primary/10">
                    <FileText className="h-8 w-8 text-primary"/>
                </div>
                <div>
                    <h1 className="text-3xl font-bold">Terms of Use</h1>
                    <p className="text-muted-foreground">Rules and guidelines for using our platform</p>
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
                                     * Path: frontend/src/pages/public/TermsOfUsePage.jsx
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

export default TermsOfUsePage;
