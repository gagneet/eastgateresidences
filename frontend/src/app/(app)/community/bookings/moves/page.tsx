"use client";
import BookingsPage from "@/pages/dashboard/BookingsPage";
/**
 * Canonical nested route for the admin_staff "Move in/out" nav item.
 * Opens the Bookings page on its Move In/Out view (real /move-bookings data).
 */
export default function Page() {
    return <BookingsPage initialTab="move"/>;
}
