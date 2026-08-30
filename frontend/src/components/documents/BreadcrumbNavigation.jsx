"use client";
import React from 'react';
import { ChevronRight, Home } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: BreadcrumbNavigation
 * Path: frontend/src/components/documents/BreadcrumbNavigation.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BreadcrumbNavigation = ({currentFolder, allFolders, onNavigate}) => {
    /**
     * @generated FunctionHeader
     * Function: buildPath
     * Path: frontend/src/components/documents/BreadcrumbNavigation.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const buildPath = (folder) => {
        if (!folder) return [];

        const path = [];
        let current = folder;
        const visited = new Set();

        while (current) {
            if (visited.has(current.id)) {
                console.error('Circular reference detected in folder hierarchy');
                break;
            }
            visited.add(current.id);
            path.unshift(current);
            current = allFolders.find(f => f.id === current.parent_folder_id);
        }

        return path;
    };

    const path = currentFolder ? buildPath(currentFolder) : [];

    return (
        <div className="flex items-center gap-1 text-sm text-muted-foreground mb-4 overflow-x-auto"
             data-testid="breadcrumb-navigation">
            <button
                onClick={() => onNavigate(null)}
                className="flex items-center gap-1 hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-muted"
                data-testid="breadcrumb-home"
            >
                <Home className="h-4 w-4"/>
                <span>Home</span>
            </button>

            {path.map((folder, index) => (
                <React.Fragment key={folder.id}>
                    <ChevronRight className="h-4 w-4 flex-shrink-0"/>
                    <button
                        onClick={() => onNavigate(folder.id)}
                        className={`hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-muted whitespace-nowrap ${
                            index === path.length - 1 ? 'text-foreground font-medium' : ''
                        }`}
                        data-testid={`breadcrumb-${folder.id}`}
                    >
                        {folder.name}
                    </button>
                </React.Fragment>
            ))}
        </div>
    );
};

export default BreadcrumbNavigation;
