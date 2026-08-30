"use client";
import BookingsPage from "@/pages/dashboard/BookingsPage";
/**
 * Canonical nested route for the guest "My stay" nav item.
 * Opens the Bookings page scoped to the current user's own bookings.
 */
export default function Page() {
    return <BookingsPage scope="mine"/>;
}
