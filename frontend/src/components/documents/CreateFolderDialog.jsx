"use client";
import React, { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import { Loader2 } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: CreateFolderDialog
 * Path: frontend/src/components/documents/CreateFolderDialog.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CreateFolderDialog = ({open, onOpenChange, onCreateFolder, currentFolder, loading}) => {
    const [formData, setFormData] = useState({
        name: '',
        description: '',
    });
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/components/documents/CreateFolderDialog.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.name.trim()) return;

        await onCreateFolder(formData);
        setFormData({name: '', description: ''});
    };
    /**
     * @generated FunctionHeader
     * Function: handleClose
     * Path: frontend/src/components/documents/CreateFolderDialog.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleClose = () => {
        setFormData({name: '', description: ''});
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={handleClose}>
            <DialogContent className="sm:max-w-[500px]">
                <DialogHeader>
                    <DialogTitle>Create New Folder</DialogTitle>
                    <DialogDescription>
                        {currentFolder
                            ? `Create a subfolder in "${currentFolder.name}"`
                            : 'Create a new folder at root level'}
                    </DialogDescription>
                </DialogHeader>

                <form onSubmit={handleSubmit}>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label htmlFor="folder-name">
                                Folder Name <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="folder-name"
                                value={formData.name}
                                onChange={(e) => setFormData({...formData, name: e.target.value})}
                                placeholder="Enter folder name"
                                required
                                autoFocus
                                data-testid="folder-name-input"
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="folder-description">Description (Optional)</Label>
                            <Textarea
                                id="folder-description"
                                value={formData.description}
                                onChange={(e) => setFormData({...formData, description: e.target.value})}
                                placeholder="Enter folder description"
                                rows={3}
                                data-testid="folder-description-input"
                            />
                        </div>

                        {currentFolder && (
                            <div className="p-3 bg-muted rounded-md">
                                <p className="text-sm text-muted-foreground">
                                    <span
                                        className="font-medium">Location:</span> {currentFolder.path || `/${currentFolder.name}`}
                                </p>
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={handleClose}
                            disabled={loading}
                        >
                            Cancel
                        </Button>
                        <Button type="submit" disabled={loading || !formData.name.trim()}
                                data-testid="create-folder-button">
                            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                            Create Folder
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
};

export default CreateFolderDialog;
