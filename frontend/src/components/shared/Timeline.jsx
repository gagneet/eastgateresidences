import React from 'react';
import { Check, CheckCircle2, Clock, FileText, Receipt, Wrench } from 'lucide-react';
import { formatDate } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: Timeline
 * Path: frontend/src/components/shared/Timeline.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const Timeline = ({events}) => {
    if (!events || events.length === 0) return null;
    /**
     * @generated FunctionHeader
     * Function: getIcon
     * Path: frontend/src/components/shared/Timeline.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getIcon = (type) => {
        switch (type) {
            case 'WORK_ORDER_CREATED':
                return <Wrench className="h-4 w-4 text-blue-500"/>;
            case 'QUOTE_SUBMITTED':
                return <FileText className="h-4 w-4 text-yellow-500"/>;
            case 'QUOTE_SELECTED':
                return <CheckCircle2 className="h-4 w-4 text-green-500"/>;
            case 'APPROVAL_DECISION':
                return <Check className="h-4 w-4 text-purple-500"/>;
            case 'INVOICE_SUBMITTED':
                return <Receipt className="h-4 w-4 text-orange-500"/>;
            case 'PAYMENT_PROCESSED':
                return <CheckCircle2 className="h-4 w-4 text-green-600"/>;
            default:
                return <Clock className="h-4 w-4 text-gray-400"/>;
        }
    };

    return (
        <div className="flow-root">
            <ul role="list" className="-mb-8">
                {events.map((event, eventIdx) => (
                    <li key={event.timestamp + eventIdx}>
                        <div className="relative pb-8">
                            {eventIdx !== events.length - 1 ? (
                                <span
                                    className="absolute left-4 top-4 -ml-px h-full w-0.5 bg-gray-200"
                                    aria-hidden="true"
                                />
                            ) : null}
                            <div className="relative flex space-x-3">
                                <div>
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-50 ring-8 ring-white">
                    {getIcon(event.type)}
                  </span>
                                </div>
                                <div className="flex min-w-0 flex-1 justify-between space-x-4 pt-1.5">
                                    <div>
                                        <p className="text-sm font-medium text-gray-900">
                                            {event.title}
                                        </p>
                                        {event.description && (
                                            <p className="mt-0.5 text-sm text-gray-500">{event.description}</p>
                                        )}
                                    </div>
                                    <div className="whitespace-nowrap text-right text-sm text-gray-500">
                                        <time dateTime={event.timestamp}>{formatDate(event.timestamp)}</time>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </li>
                ))}
            </ul>
        </div>
    );
};

export default Timeline;
