"use client";
// Inspired by react-hot-toast library
import * as React from "react"

const TOAST_LIMIT = 1
const TOAST_REMOVE_DELAY = 1000000

type ActionType = "ADD_TOAST" | "UPDATE_TOAST" | "DISMISS_TOAST" | "REMOVE_TOAST";

interface ToastAction {
    type: ActionType;
    toast?: any;
    toastId?: string;
}

let count = 0
/**
 * @generated FunctionHeader
 * Function: genId
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function genId() {
    count = (count + 1) % Number.MAX_SAFE_INTEGER
    return count.toString();
}

const toastTimeouts = new Map<string, NodeJS.Timeout>()
/**
 * @generated FunctionHeader
 * Function: addToRemoveQueue
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const addToRemoveQueue = (toastId: string) => {
    if (toastTimeouts.has(toastId)) {
        return
    }

    const timeout = setTimeout(() => {
        toastTimeouts.delete(toastId)
        dispatch({
            type: "REMOVE_TOAST",
            toastId: toastId,
        })
    }, TOAST_REMOVE_DELAY)

    toastTimeouts.set(toastId, timeout)
}
/**
 * @generated FunctionHeader
 * Function: reducer
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const reducer = (state: any, action: ToastAction) => {
    switch (action.type) {
        case "ADD_TOAST":
            return {
                ...state,
                toasts: [action.toast, ...state.toasts].slice(0, TOAST_LIMIT),
            };

        case "UPDATE_TOAST":
            return {
                ...state,
                toasts: state.toasts.map((t: any) =>
                    t.id === action.toast.id ? {...t, ...action.toast} : t),
            };

        case "DISMISS_TOAST": {
            const {toastId} = action

            // ! Side effects ! - This could be extracted into a dismissToast() action,
            // but I'll keep it here for simplicity
            if (toastId) {
                addToRemoveQueue(toastId)
            } else {
                state.toasts.forEach((toast: any) => {
                    addToRemoveQueue(toast.id)
                })
            }

            return {
                ...state,
                toasts: state.toasts.map((t: any) =>
                    t.id === toastId || toastId === undefined
                        ? {
                            ...t,
                            open: false,
                        }
                        : t),
            };
        }
        case "REMOVE_TOAST":
            if (action.toastId === undefined) {
                return {
                    ...state,
                    toasts: [],
                }
            }
            return {
                ...state,
                toasts: state.toasts.filter((t: any) => t.id !== action.toastId),
            };
        default:
            return state;
    }
}

const listeners: Array<(state: any) => void> = []

let memoryState = {toasts: []}
/**
 * @generated FunctionHeader
 * Function: dispatch
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function dispatch(action: ToastAction) {
    memoryState = reducer(memoryState, action)
    listeners.forEach((listener) => {
        listener(memoryState)
    })
}
/**
 * @generated FunctionHeader
 * Function: toast
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function toast({
                   ...props
               }: any) {
    const id = genId()
    /**
     * @generated FunctionHeader
     * Function: update
     * Path: frontend/src/hooks/use-toast.ts
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const update = (props: any) =>
        dispatch({
            type: "UPDATE_TOAST",
            toast: {...props, id},
        })
    /**
     * @generated FunctionHeader
     * Function: dismiss
     * Path: frontend/src/hooks/use-toast.ts
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const dismiss = () => dispatch({type: "DISMISS_TOAST", toastId: id})

    dispatch({
        type: "ADD_TOAST",
        toast: {
            ...props,
            id,
            open: true,

            onOpenChange: (open: boolean) => {
                if (!open) dismiss()
            },
        },
    })

    return {
        id: id,
        dismiss,
        update,
    }
}
/**
 * @generated FunctionHeader
 * Function: useToast
 * Path: frontend/src/hooks/use-toast.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function useToast() {
    const [state, setState] = React.useState(memoryState)

    React.useEffect(() => {
        listeners.push(setState)
        return () => {
            const index = listeners.indexOf(setState)
            if (index > -1) {
                listeners.splice(index, 1)
            }
        };
    }, [state])

    return {
        ...state,
        toast,

        dismiss: (toastId?: string) => dispatch({type: "DISMISS_TOAST", toastId}),
    };
}

export {useToast, toast}
