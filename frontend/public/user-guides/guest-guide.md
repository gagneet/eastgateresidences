# Guest Guide — EastGate Residences Portal

**For:** Guests at 14 Hoolihan Street, Denman Prospect ACT  
**Role:** `guest`  
**URL:** https://eastgateresidences.com.au/dashboard

---

## 1. What You Can Access

Guest accounts have limited, read-only access to community information. The following features are available:

| Feature                      | Access level                                            |
|------------------------------|---------------------------------------------------------|
| Community announcements      | ✅ Read-only                                             |
| Resident directory           | ✅ Name and unit only                                    |
| Community events calendar    | ✅ View only                                             |
| Public notices               | ✅ Read-only                                             |
| Emergency services directory | ✅ Full access                                           |
| Parcel notifications         | ✅ Bell notification when a parcel arrives for your unit |
| Maintenance requests         | ❌ Not available                                         |
| Voting on proposals          | ❌ Not available                                         |
| Financial data               | ❌ Not available                                         |
| Chat & messages              | ❌ Not available                                         |
| Smart Request form           | ❌ Not available                                         |
| Volunteer events             | ❌ Not available                                         |

If you need access to additional features, ask the resident who sponsored your guest account to contact the Strata
Manager.

---

## 2. Parcel Notifications

When a parcel is logged at the front desk for your unit, you will automatically receive a **bell notification** in the
portal. The notification includes:

- The courier name (e.g. Australia Post, DHL)
- A description of the package (if logged)
- The tracking number (if provided)
- A note to collect the parcel from the front desk

You can also view your parcel status and history at **Dashboard → Parcels**.

---

## 3. How Access Expires (JWT Token Hard Cap)

Guest accounts have a **hard cap of 364 days** from the date the account was created. This limit is enforced at the
security token (JWT) level — your session token will automatically expire no later than 364 days after account creation,
regardless of any other settings.

Your end date is set at registration and cannot exceed 364 days from today.

| Event            | Timing                  |
|------------------|-------------------------|
| Email reminder   | 30 days before expiry   |
| Dashboard banner | 7 days before expiry    |
| Auto-archive     | On the expiry date      |
| Data purge       | 90 days after archiving |

On the expiry date, your account is automatically archived. You will be logged out and cannot log back in.

Archived accounts retain no personal data after 90 days (in accordance with the platform's privacy policy).

---

## 4. How to Extend Access

To request an extension before your account expires:

1. Contact the **Strata Manager**
   at [strata.manager@eastgateresidences.com.au](mailto:strata.manager@eastgateresidences.com.au).
2. Include your name, registered email address, and the reason for the extension request.
3. The Strata Manager will evaluate the request and, if approved, create a new guest account for you.

> Note: Extensions are not automatic. Each new access period requires explicit approval. The 364-day maximum is a hard
> security limit and cannot be overridden.

---

## 5. Contact Information

| Contact                     | Details                                                                                     |
|-----------------------------|---------------------------------------------------------------------------------------------|
| **Strata Manager**          | [strata.manager@eastgateresidences.com.au](mailto:strata.manager@eastgateresidences.com.au) |
| **Emergency (after hours)** | See `/emergency-services` on the portal                                                     |
| **Technical support**       | [support@silverfoxtechnologies.com.au](mailto:support@silverfoxtechnologies.com.au)         |
