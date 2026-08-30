"use client";
import BookingsPage from "@/pages/dashboard/BookingsPage";
/**
 * Canonical nested route for the admin_staff "Visitors" nav item.
 * Opens the Bookings page on the Amenities view filtered to Visitor Parking.
 */
export default function Page() {
    return <BookingsPage initialTab="amenity" initialAmenityFilter="visitor_parking"/>;
}
