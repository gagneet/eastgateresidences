import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../../components/ui/dialog';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Download, FileText, FolderPlus, Grid3x3, List, Loader2, Trash2, Upload } from 'lucide-react';
import { documentCategories, formatDate } from '../../lib/utils';
import { toast } from 'sonner';
import FolderTree from '../../components/documents/FolderTree';
import BreadcrumbNavigation from '../../components/documents/BreadcrumbNavigation';
import CreateFolderDialog from '../../components/documents/CreateFolderDialog';
/**
 * @generated FunctionHeader
 * Function: DocumentsPage
 * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DocumentsPage = () => {
    const {user, api, hasPermission} = useAuth();

    // State
    const [documents, setDocuments] = useState([]);
    const [folders, setFolders] = useState([]);
    const [currentFolderId, setCurrentFolderId] = useState(null);
    const [loading, setLoading] = useState(true);
    const [uploadOpen, setUploadOpen] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [createFolderOpen, setCreateFolderOpen] = useState(false);
    const [creatingFolder, setCreatingFolder] = useState(false);
    const [viewMode, setViewMode] = useState('grid');
    const [searchTerm, setSearchTerm] = useState('');

    const [formData, setFormData] = useState({
        title: '',
        description: '',
        category: 'public_documents',
        file: null
    });

    const canUpload = hasPermission('can_upload_documents');
    const currentFolder = folders.find(f => f.id === currentFolderId);

    // Fetch data
    useEffect(() => {
        fetchFolders();
        fetchDocuments();
    }, [currentFolderId]);
    /**
     * @generated FunctionHeader
     * Function: fetchFolders
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchFolders = async () => {
        try {
            const response = await api.get('/folders');
            setFolders(response.data);
        } catch (error) {
            console.error('Failed to fetch folders:', error);
            toast.error('Failed to load folders');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: fetchDocuments
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchDocuments = async () => {
        try {
            setLoading(true);
            const params = currentFolderId ? `?folder_id=${currentFolderId}` : '';
            const response = await api.get(`/documents${params}`);
            setDocuments(response.data);
        } catch (error) {
            console.error('Failed to fetch documents:', error);
            toast.error('Failed to load documents');
        } finally {
            setLoading(false);
        }
    };
    // Handlers
    /**
     * @generated FunctionHeader
     * Function: handleFolderClick
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFolderClick = (folderId) => {
        setCurrentFolderId(folderId);
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreateFolder
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateFolder = async (folderData) => {
        setCreatingFolder(true);
        try {
            await api.post('/folders', {
                name: folderData.name,
                description: folderData.description,
                parent_folder_id: currentFolderId
            });
            toast.success('Folder created successfully');
            setCreateFolderOpen(false);
            fetchFolders();
        } catch (error) {
            console.error('Failed to create folder:', error);
            toast.error(error.response?.data?.detail || 'Failed to create folder');
        } finally {
            setCreatingFolder(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleUpload
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpload = async (e) => {
        e.preventDefault();
        if (!formData.file) {
            toast.error('Please select a file');
            return;
        }

        setUploading(true);
        try {
            const uploadData = new FormData();
            uploadData.append('title', formData.title);
            uploadData.append('description', formData.description || '');
            uploadData.append('category', formData.category);
            if (currentFolderId) {
                uploadData.append('folder_id', currentFolderId);
            }
            uploadData.append('file', formData.file);

            await api.post('/documents', uploadData, {
                headers: {'Content-Type': 'multipart/form-data'}
            });

            toast.success('Document uploaded successfully');
            setUploadOpen(false);
            setFormData({title: '', description: '', category: 'public_documents', file: null});
            fetchDocuments();
            fetchFolders(); // Refresh to update document counts
        } catch (error) {
            console.error('Upload failed:', error);
            toast.error(error.response?.data?.detail || 'Failed to upload document');
        } finally {
            setUploading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownload
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownload = async (docId, fileName) => {
        try {
            const response = await api.get(`/documents/${docId}`);
            const doc = response.data;

            if (doc.file_data) {
                const link = document.createElement('a');
                link.href = `data:${doc.file_type};base64,${doc.file_data}`;
                link.download = fileName;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                toast.success('Download started');
            }
        } catch (error) {
            console.error('Download failed:', error);
            toast.error('Failed to download document');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/DocumentsPageV2.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (docId) => {
        if (!window.confirm('Are you sure you want to delete this document?')) return;

        try {
            await api.delete(`/documents/${docId}`);
            toast.success('Document deleted');
            fetchDocuments();
            fetchFolders();
        } catch (error) {
            console.error('Delete failed:', error);
            toast.error('Failed to delete document');
        }
    };

    const filteredDocuments = documents.filter(doc =>
        doc.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
        doc.file_name.toLowerCase().includes(searchTerm.toLowerCase())
    );

    return (
        <div className="flex h-[calc(100vh-200px)] gap-6">
            {/* Left Sidebar - Folder Tree */}
            <div className="w-1/4 border-r pr-4 flex flex-col">
                <div className="mb-4">
                    <h2 className="text-lg font-semibold mb-2">Folders</h2>
                    {canUpload && (
                        <Button
                            onClick={() => setCreateFolderOpen(true)}
                            variant="outline"
                            size="sm"
                            className="w-full"
                            data-testid="create-folder-btn"
                        >
                            <FolderPlus className="mr-2 h-4 w-4"/>
                            New Folder
                        </Button>
                    )}
                </div>

                <div className="flex-1 overflow-y-auto">
                    <FolderTree
                        folders={folders}
                        currentFolderId={currentFolderId}
                        onFolderClick={handleFolderClick}
                    />
                </div>
            </div>

            {/* Right Content Area */}
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* Breadcrumb */}
                <BreadcrumbNavigation
                    currentFolder={currentFolder}
                    allFolders={folders}
                    onNavigate={handleFolderClick}
                />

                {/* Toolbar */}
                <div className="flex justify-between items-center mb-4">
                    <div className="flex gap-2">
                        {canUpload && (
                            <>
                                <Button onClick={() => setUploadOpen(true)} data-testid="upload-btn">
                                    <Upload className="mr-2 h-4 w-4"/>
                                    Upload File
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => setCreateFolderOpen(true)}
                                    data-testid="new-subfolder-btn"
                                >
                                    <FolderPlus className="mr-2 h-4 w-4"/>
                                    New Subfolder
                                </Button>
                            </>
                        )}
                    </div>

                    <div className="flex gap-2 items-center">
                        <Input
                            placeholder="Search documents..."
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            className="w-64"
                            data-testid="search-input"
                        />
                        <Select value={viewMode} onValueChange={setViewMode}>
                            <SelectTrigger className="w-32">
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="grid">
                                    <div className="flex items-center">
                                        <Grid3x3 className="mr-2 h-4 w-4"/>
                                        Grid
                                    </div>
                                </SelectItem>
                                <SelectItem value="list">
                                    <div className="flex items-center">
                                        <List className="mr-2 h-4 w-4"/>
                                        List
                                    </div>
                                </SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </div>

                {/* Documents */}
                <div className="flex-1 overflow-y-auto">
                    {loading ? (
                        <div className="flex items-center justify-center h-full">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                        </div>
                    ) : filteredDocuments.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-center">
                            <FileText className="h-16 w-16 text-muted-foreground/50 mb-4"/>
                            <h3 className="text-lg font-medium mb-2">No documents</h3>
                            <p className="text-muted-foreground mb-4">
                                {currentFolder
                                    ? `This folder is empty. Upload files to get started.`
                                    : 'Upload your first document to get started.'}
                            </p>
                            {canUpload && (
                                <Button onClick={() => setUploadOpen(true)}>
                                    <Upload className="mr-2 h-4 w-4"/>
                                    Upload Document
                                </Button>
                            )}
                        </div>
                    ) : viewMode === 'grid' ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                            {filteredDocuments.map((doc) => (
                                <Card key={doc.id} className="hover:shadow-lg transition-shadow">
                                    <CardContent className="p-4">
                                        <div className="flex items-start justify-between mb-2">
                                            <FileText className="h-8 w-8 text-primary flex-shrink-0"/>
                                            <div className="flex gap-1">
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={() => handleDownload(doc.id, doc.file_name)}
                                                    data-testid={`download-${doc.id}`}
                                                >
                                                    <Download className="h-4 w-4"/>
                                                </Button>
                                                {canUpload && (
                                                    <Button
                                                        size="sm"
                                                        variant="ghost"
                                                        onClick={() => handleDelete(doc.id)}
                                                        data-testid={`delete-${doc.id}`}
                                                    >
                                                        <Trash2 className="h-4 w-4 text-destructive"/>
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                        <h3 className="font-medium text-sm mb-1 truncate" title={doc.title}>
                                            {doc.title}
                                        </h3>
                                        <p className="text-xs text-muted-foreground mb-2 truncate">
                                            {doc.file_name}
                                        </p>
                                        <div
                                            className="flex items-center justify-between text-xs text-muted-foreground">
                                            <span>{formatDate(doc.created_at)}</span>
                                            <span>{( doc.file_size / 1024 ).toFixed(1)} KB</span>
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {filteredDocuments.map((doc) => (
                                <Card key={doc.id} className="hover:shadow-md transition-shadow">
                                    <CardContent className="p-4 flex items-center justify-between">
                                        <div className="flex items-center gap-3 flex-1 min-w-0">
                                            <FileText className="h-5 w-5 text-primary flex-shrink-0"/>
                                            <div className="flex-1 min-w-0">
                                                <h3 className="font-medium text-sm truncate">{doc.title}</h3>
                                                <p className="text-xs text-muted-foreground truncate">{doc.file_name}</p>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-4 flex-shrink-0">
                      <span className="text-xs text-muted-foreground">
                        {formatDate(doc.created_at)}
                      </span>
                                            <span className="text-xs text-muted-foreground">
                        {( doc.file_size / 1024 ).toFixed(1)} KB
                      </span>
                                            <div className="flex gap-1">
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={() => handleDownload(doc.id, doc.file_name)}
                                                >
                                                    <Download className="h-4 w-4"/>
                                                </Button>
                                                {canUpload && (
                                                    <Button
                                                        size="sm"
                                                        variant="ghost"
                                                        onClick={() => handleDelete(doc.id)}
                                                    >
                                                        <Trash2 className="h-4 w-4 text-destructive"/>
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* Upload Dialog */}
            <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Upload Document</DialogTitle>
                        <DialogDescription>
                            {currentFolder
                                ? `Upload to: ${currentFolder.path || `/${currentFolder.name}`}`
                                : 'Upload to root level'}
                        </DialogDescription>
                    </DialogHeader>

                    <form onSubmit={handleUpload}>
                        <div className="space-y-4 py-4">
                            <div>
                                <Label htmlFor="title">Title *</Label>
                                <Input
                                    id="title"
                                    value={formData.title}
                                    onChange={(e) => setFormData({...formData, title: e.target.value})}
                                    required
                                />
                            </div>

                            <div>
                                <Label htmlFor="description">Description</Label>
                                <Textarea
                                    id="description"
                                    value={formData.description}
                                    onChange={(e) => setFormData({...formData, description: e.target.value})}
                                    rows={3}
                                />
                            </div>

                            <div>
                                <Label htmlFor="category">Category</Label>
                                <Select
                                    value={formData.category}
                                    onValueChange={(value) => setFormData({...formData, category: value})}
                                >
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {documentCategories.map((cat) => (
                                            <SelectItem key={cat.value} value={cat.value}>
                                                {cat.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div>
                                <Label htmlFor="file">File *</Label>
                                <Input
                                    id="file"
                                    type="file"
                                    onChange={(e) => setFormData({...formData, file: e.target.files[ 0 ]})}
                                    required
                                />
                            </div>
                        </div>

                        <div className="flex justify-end gap-2">
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setUploadOpen(false)}
                                disabled={uploading}
                            >
                                Cancel
                            </Button>
                            <Button type="submit" disabled={uploading}>
                                {uploading && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                                Upload
                            </Button>
                        </div>
                    </form>
                </DialogContent>
            </Dialog>

            {/* Create Folder Dialog */}
            <CreateFolderDialog
                open={createFolderOpen}
                onOpenChange={setCreateFolderOpen}
                onCreateFolder={handleCreateFolder}
                currentFolder={currentFolder}
                loading={creatingFolder}
            />
        </div>
    );
};

export default DocumentsPage;
