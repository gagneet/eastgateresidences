"use client";
import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FolderOpen } from 'lucide-react';
import { cn } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: FolderTreeNode
 * Path: frontend/src/components/documents/FolderTree.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FolderTreeNode = ({folder, level = 0, isExpanded, onToggle, onFolderClick, currentFolderId, allFolders}) => {
    const children = allFolders.filter(f => f.parent_folder_id === folder.id);
    const hasChildren = children.length > 0;
    const isSelected = currentFolderId === folder.id;
    const expanded = isExpanded(folder.id);

    return (
        <div>
            <div
                className={cn(
                    "flex items-center gap-1 px-2 py-1.5 rounded cursor-pointer hover:bg-muted/50 transition-colors",
                    isSelected && "bg-muted font-medium"
                )}
                style={{paddingLeft: `${level * 16 + 8}px`}}
                onClick={() => onFolderClick(folder.id)}
                data-testid={`folder-${folder.id}`}
            >
                {hasChildren ? (
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggle(folder.id);
                        }}
                        className="p-0 hover:bg-transparent"
                        data-testid={`folder-toggle-${folder.id}`}
                    >
                        {expanded ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground"/>
                        ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground"/>
                        )}
                    </button>
                ) : (
                    <span className="w-4"/>
                )}

                {expanded && hasChildren ? (
                    <FolderOpen className="h-4 w-4 text-muted-foreground" style={{color: folder.color || undefined}}/>
                ) : (
                    <Folder className="h-4 w-4 text-muted-foreground" style={{color: folder.color || undefined}}/>
                )}

                <span className="text-sm truncate flex-1">{folder.name}</span>

                {folder.document_count > 0 && (
                    <span className="text-xs text-muted-foreground">
            {folder.document_count}
          </span>
                )}
            </div>

            {expanded && hasChildren && (
                <div>
                    {children
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .map(child => (
                            <FolderTreeNode
                                key={child.id}
                                folder={child}
                                level={level + 1}
                                isExpanded={isExpanded}
                                onToggle={onToggle}
                                onFolderClick={onFolderClick}
                                currentFolderId={currentFolderId}
                                allFolders={allFolders}
                            />
                        ))}
                </div>
            )}
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: FolderTree
 * Path: frontend/src/components/documents/FolderTree.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FolderTree = ({folders, currentFolderId, onFolderClick}) => {
    const [expandedFolders, setExpandedFolders] = useState(new Set());
    /**
     * @generated FunctionHeader
     * Function: isExpanded
     * Path: frontend/src/components/documents/FolderTree.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isExpanded = (folderId) => expandedFolders.has(folderId);
    /**
     * @generated FunctionHeader
     * Function: toggleFolder
     * Path: frontend/src/components/documents/FolderTree.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleFolder = (folderId) => {
        setExpandedFolders(prev => {
            const next = new Set(prev);
            if (next.has(folderId)) {
                next.delete(folderId);
            } else {
                next.add(folderId);
            }
            return next;
        });
    };

    const rootFolders = folders
        .filter(f => !f.parent_folder_id)
        .sort((a, b) => a.name.localeCompare(b.name));

    return (
        <div className="space-y-1" data-testid="folder-tree">
            {/* Root level - All Documents */}
            <div
                className={cn(
                    "flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-muted/50 transition-colors",
                    currentFolderId === null && "bg-muted font-medium"
                )}
                onClick={() => onFolderClick(null)}
                data-testid="folder-root"
            >
                <FolderOpen className="h-4 w-4 text-muted-foreground"/>
                <span className="text-sm">All Documents</span>
            </div>

            {/* Folder tree */}
            {rootFolders.map(folder => (
                <FolderTreeNode
                    key={folder.id}
                    folder={folder}
                    level={0}
                    isExpanded={isExpanded}
                    onToggle={toggleFolder}
                    onFolderClick={onFolderClick}
                    currentFolderId={currentFolderId}
                    allFolders={folders}
                />
            ))}
        </div>
    );
};

export default FolderTree;
