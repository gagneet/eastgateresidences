'use client';

import React, { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Bold, Heading1, Heading2, Italic, List, ListOrdered, Quote, Redo, Undo } from 'lucide-react';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../ui/tooltip';
/**
 * @generated FunctionHeader
 * Function: ToolbarButton
 * Path: frontend/src/components/shared/RichTextEditor.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ToolbarButton = ({
                           onClick,
                           disabled,
                           isActive,
                           label,
                           children,
                           type = "button"
                       }) => (
    <Tooltip>
        <TooltipTrigger asChild>
            <Button
                type={type}
                variant="ghost"
                size="sm"
                onClick={onClick}
                disabled={disabled}
                className={isActive ? 'bg-muted' : ''}
                aria-label={label}
            >
                {children}
            </Button>
        </TooltipTrigger>
        <TooltipContent side="top">
            <p>{label}</p>
        </TooltipContent>
    </Tooltip>
);
/**
 * @generated FunctionHeader
 * Function: MenuBar
 * Path: frontend/src/components/shared/RichTextEditor.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MenuBar = ({editor}) => {
    if (!editor) {
        return null;
    }

    return (
        <TooltipProvider delayDuration={400}>
            <div className="flex flex-wrap gap-1 p-1 border-b bg-muted/50 rounded-t-md">
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleBold().run()}
                    disabled={!editor.can().chain().focus().toggleBold().run()}
                    isActive={editor.isActive('bold')}
                    label="Bold"
                >
                    <Bold className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleItalic().run()}
                    disabled={!editor.can().chain().focus().toggleItalic().run()}
                    isActive={editor.isActive('italic')}
                    label="Italic"
                >
                    <Italic className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleHeading({level: 1}).run()}
                    isActive={editor.isActive('heading', {level: 1})}
                    label="Heading 1"
                >
                    <Heading1 className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleHeading({level: 2}).run()}
                    isActive={editor.isActive('heading', {level: 2})}
                    label="Heading 2"
                >
                    <Heading2 className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleBulletList().run()}
                    isActive={editor.isActive('bulletList')}
                    label="Bullet List"
                >
                    <List className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleOrderedList().run()}
                    isActive={editor.isActive('orderedList')}
                    label="Numbered List"
                >
                    <ListOrdered className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().toggleBlockquote().run()}
                    isActive={editor.isActive('blockquote')}
                    label="Blockquote"
                >
                    <Quote className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().undo().run()}
                    disabled={!editor.can().chain().focus().undo().run()}
                    label="Undo"
                >
                    <Undo className="h-4 w-4"/>
                </ToolbarButton>
                <ToolbarButton
                    onClick={() => editor.chain().focus().redo().run()}
                    disabled={!editor.can().chain().focus().redo().run()}
                    label="Redo"
                >
                    <Redo className="h-4 w-4"/>
                </ToolbarButton>
            </div>
        </TooltipProvider>
    );
};
/**
 * @generated FunctionHeader
 * Function: RichTextEditor
 * Path: frontend/src/components/shared/RichTextEditor.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RichTextEditor = ({content, onChange, placeholder = 'Start typing...'}) => {
    const editor = useEditor({
        extensions: [
            StarterKit,
        ],
        content: content,

        onUpdate: ({editor}) => {
            onChange(editor.getHTML());
        },
        editorProps: {
            attributes: {
                class: 'prose prose-sm dark:prose-invert max-w-none p-4 focus:outline-none min-h-[150px]',
            },
        },
    });

    // Sync content if it changes externally
    useEffect(() => {
        if (editor && content !== editor.getHTML()) {
            editor.commands.setContent(content);
        }
    }, [content, editor]);

    return (
        <div className="border rounded-md focus-within:ring-1 focus-within:ring-ring transition-all bg-background">
            <MenuBar editor={editor}/>
            <EditorContent editor={editor}/>
        </div>
    );
};

export default RichTextEditor;
