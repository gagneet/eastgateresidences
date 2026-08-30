"use client";
import React, { useEffect, useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';
import { useAuth } from '../../contexts/AuthContext';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Button } from '../ui/button';
import { Alert, AlertDescription } from '../ui/alert';
import { CheckCircle, CreditCard, Loader2, XCircle } from 'lucide-react';
import { formatCurrency } from '../../lib/utils';
import { toast } from 'sonner';

// Initialize Stripe with publishable key
// Next.js uses NEXT_PUBLIC_ prefix; CRA used REACT_APP_ — support both
const STRIPE_PK = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || process.env.REACT_APP_STRIPE_PUBLISHABLE_KEY || '';
const stripePromise = STRIPE_PK ? loadStripe(STRIPE_PK) : null;
/**
 * Payment Form Component
 * Handles the actual payment submission using Stripe Elements
 */
const PaymentForm = ({clientSecret, amount, onSuccess, onError}) => {
    const stripe = useStripe();
    const elements = useElements();
    const [processing, setProcessing] = useState(false);
    const [error, setError] = useState(null);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/components/payments/PaymentModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();

        if (!stripe || !elements) {
            return;
        }

        setProcessing(true);
        setError(null);

        try {
            const {error: submitError, paymentIntent} = await stripe.confirmPayment({
                elements,
                confirmParams: {
                    return_url: `${window.location.origin}/dashboard`,
                },
                redirect: 'if_required'
            });

            if (submitError) {
                setError(submitError.message);
                onError(submitError.message);
            } else if (paymentIntent && paymentIntent.status === 'succeeded') {
                onSuccess(paymentIntent);
            }
        } catch (err) {
            setError(err.message);
            onError(err.message);
        } finally {
            setProcessing(false);
        }
    };

    return (
        <form onSubmit={handleSubmit} className="space-y-6">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-blue-900">Amount to Pay:</span>
                    <span className="text-2xl font-bold text-blue-900">
            {formatCurrency(amount)}
          </span>
                </div>
            </div>

            <PaymentElement/>

            {error && (
                <Alert variant="destructive">
                    <XCircle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            <div className="flex gap-3">
                <Button
                    type="submit"
                    disabled={!stripe || processing}
                    className="flex-1"
                    size="lg"
                >
                    {processing ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                            Processing...
                        </>
                    ) : (
                        <>
                            <CreditCard className="mr-2 h-4 w-4"/>
                            Pay {formatCurrency(amount)}
                        </>
                    )}
                </Button>
            </div>

            <p className="text-xs text-center text-muted-foreground">
                Powered by Stripe. Your payment information is secure and encrypted.
            </p>
        </form>
    );
};
/**
 * Payment Modal Component
 * Main component for handling levy payments
 */
const PaymentModal = ({isOpen, onClose, paymentData, onPaymentSuccess}) => {
    const {api} = useAuth();
    const [clientSecret, setClientSecret] = useState(null);
    const [loading, setLoading] = useState(false);
    const [paymentStatus, setPaymentStatus] = useState(null); // null, 'success', 'error'
    const [error, setError] = useState(null);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: createPaymentIntent
         * Path: frontend/src/components/payments/PaymentModal.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const createPaymentIntent = async () => {
            setLoading(true);
            setError(null);

            try {
                const response = await api.post('/payments/create-intent', {
                    unit_number: paymentData.unit_number,
                    amount: paymentData.amount,
                    levy_period: paymentData.levy_period || 'Current Period',
                    admin_fund_amount: paymentData.admin_fund_amount,
                    sinking_fund_amount: paymentData.sinking_fund_amount,
                    description: paymentData.description || 'Levy Payment'
                });

                setClientSecret(response.data.client_secret);
            } catch (err) {
                console.error('Failed to create payment intent:', err);
                setError(err.response?.data?.detail || 'Failed to initialize payment. Please try again.');
                toast.error('Failed to initialize payment');
            } finally {
                setLoading(false);
            }
        };

        if (isOpen && paymentData) {
            createPaymentIntent();
        } else {
            // Reset state when modal closes
            setClientSecret(null);
            setPaymentStatus(null);
            setError(null);
        }
    }, [isOpen, paymentData, api]);
    /**
     * @generated FunctionHeader
     * Function: handlePaymentSuccess
     * Path: frontend/src/components/payments/PaymentModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePaymentSuccess = (paymentIntent) => {
        setPaymentStatus('success');
        toast.success('Payment successful!');

        // Call parent callback after a short delay to show success message
        setTimeout(() => {
            onPaymentSuccess && onPaymentSuccess(paymentIntent);
            onClose();
        }, 2000);
    };
    /**
     * @generated FunctionHeader
     * Function: handlePaymentError
     * Path: frontend/src/components/payments/PaymentModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePaymentError = (errorMessage) => {
        setPaymentStatus('error');
        setError(errorMessage);
        toast.error('Payment failed');
    };

    const stripeOptions = {
        clientSecret,
        appearance: {
            theme: 'stripe',
            variables: {
                colorPrimary: '#2F4F4F',
                colorBackground: '#ffffff',
                colorText: '#1f2937',
                colorDanger: '#ef4444',
                fontFamily: 'Inter, system-ui, sans-serif',
                spacingUnit: '4px',
                borderRadius: '8px',
            },
        },
    };

    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[500px]">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <CreditCard className="h-5 w-5"/>
                        {paymentStatus === 'success' ? 'Payment Successful' : 'Make Payment'}
                    </DialogTitle>
                    <DialogDescription>
                        {paymentStatus === 'success'
                            ? 'Your payment has been processed successfully.'
                            : 'Pay your levy using a credit or debit card.'}
                    </DialogDescription>
                </DialogHeader>

                <div className="py-4">
                    {loading && (
                        <div className="flex flex-col items-center justify-center py-12">
                            <Loader2 className="h-12 w-12 animate-spin text-primary mb-4"/>
                            <p className="text-sm text-muted-foreground">Initializing payment...</p>
                        </div>
                    )}

                    {error && !clientSecret && (
                        <Alert variant="destructive">
                            <XCircle className="h-4 w-4"/>
                            <AlertDescription>{error}</AlertDescription>
                        </Alert>
                    )}

                    {paymentStatus === 'success' && (
                        <div className="flex flex-col items-center justify-center py-12">
                            <CheckCircle className="h-16 w-16 text-green-600 mb-4"/>
                            <h3 className="text-lg font-semibold mb-2">Payment Successful!</h3>
                            <p className="text-sm text-muted-foreground text-center">
                                Your levy payment has been processed. A receipt has been sent to your email.
                            </p>
                        </div>
                    )}

                    {clientSecret && !paymentStatus && !stripePromise && (
                        <Alert variant="destructive">
                            <XCircle className="h-4 w-4"/>
                            <AlertDescription>Stripe is not configured. Please contact the strata
                                manager.</AlertDescription>
                        </Alert>
                    )}

                    {clientSecret && !paymentStatus && stripePromise && (
                        <Elements stripe={stripePromise} options={stripeOptions}>
                            <PaymentForm
                                clientSecret={clientSecret}
                                amount={paymentData.amount}
                                onSuccess={handlePaymentSuccess}
                                onError={handlePaymentError}
                            />
                        </Elements>
                    )}
                </div>

                {!paymentStatus && (
                    <div className="flex justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={onClose}
                            disabled={loading}
                        >
                            Cancel
                        </Button>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
};

export default PaymentModal;
