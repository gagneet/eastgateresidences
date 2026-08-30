import React, { useCallback, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Checkbox } from '../../components/ui/checkbox';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Calendar, ClipboardList, Loader2, Plus, Trash2, User } from 'lucide-react';
import { formatDateTime, priorityConfig, statusConfig } from '../../lib/utils';
import { toast } from 'sonner';
import { useFormData } from '../../hooks/useFormData';
import { useApiData } from '../../hooks/useApiData';
/**
 * @generated FunctionHeader
 * Function: TodosPage
 * Path: frontend/src/pages/dashboard/TodosPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TodosPage = () => {
    const {api, hasPermission} = useAuth();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [filter, setFilter] = useState('all');

    // Use custom hooks for cleaner state management
    const {formData, handleChange, resetForm, handleSubmit, isSubmitting} = useFormData({
        title: '',
        description: '',
        assigned_to: '',
        due_date: '',
        priority: 'normal'
    });

    // Memoize fetch function to prevent infinite loops
    const fetchTodos = useCallback(() => {
        const params = filter !== 'all' ? `?status=${filter}` : '';
        return api.get(`/todos${params}`);
    }, [api, filter]);

    // Fetch todos with automatic refetch on filter change
    const {data: todos, loading, refetch: refetchTodos} = useApiData(
        fetchTodos,
        {dependencies: [filter]}
    );

    // Fetch users conditionally - only if user has permission
    const fetchUsers = useCallback(() => {
        return api.get('/users');
    }, [api]);

    const {data: users} = useApiData(
        fetchUsers,
        {
            fetchOnMount: hasPermission('can_manage_users'),
            dependencies: []
        }
    );

    const onSubmit = handleSubmit(async (data) => {
        await api.post('/todos', {
            ...data,
            assigned_to: data.assigned_to || null
        });
        toast.success('Task created successfully');
        setDialogOpen(false);
        resetForm();
        refetchTodos();
    });
    /**
     * @generated FunctionHeader
     * Function: updateTodoStatus
     * Path: frontend/src/pages/dashboard/TodosPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateTodoStatus = async (todoId, status) => {
        try {
            await api.put(`/todos/${todoId}?status=${status}`);
            toast.success('Task updated');
            refetchTodos();
        } catch (error) {
            toast.error('Failed to update task');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: deleteTodo
     * Path: frontend/src/pages/dashboard/TodosPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const deleteTodo = async (todoId) => {
        if (!confirm('Are you sure you want to delete this task?')) return;

        try {
            await api.delete(`/todos/${todoId}`);
            toast.success('Task deleted');
            refetchTodos();
        } catch (error) {
            toast.error('Failed to delete task');
        }
    };

    return (
        <div className="space-y-6" data-testid="todos-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Tasks</h1>
                    <p className="text-muted-foreground">EC action items and follow-ups</p>
                </div>
                <div className="flex items-center gap-4">
                    <Select value={filter} onValueChange={setFilter}>
                        <SelectTrigger className="w-[150px]" data-testid="status-filter">
                            <SelectValue placeholder="Filter"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Tasks</SelectItem>
                            <SelectItem value="pending">Pending</SelectItem>
                            <SelectItem value="in_progress">In Progress</SelectItem>
                            <SelectItem value="completed">Completed</SelectItem>
                        </SelectContent>
                    </Select>
                    {hasPermission('can_manage_meetings') && (
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button data-testid="create-todo-btn">
                                    <Plus className="mr-2 h-4 w-4"/>
                                    Add Task
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Create Task</DialogTitle>
                                    <DialogDescription>Add a new action item</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={onSubmit} className="space-y-4" data-testid="todo-form">
                                    <div className="space-y-2">
                                        <Label htmlFor="title">Title</Label>
                                        <Input
                                            id="title"
                                            name="title"
                                            value={formData.title}
                                            onChange={handleChange}
                                            required
                                            data-testid="todo-title-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="description">Description</Label>
                                        <Textarea
                                            id="description"
                                            name="description"
                                            value={formData.description}
                                            onChange={handleChange}
                                            data-testid="todo-description-input"
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="priority">Priority</Label>
                                            <Select
                                                value={formData.priority}
                                                onValueChange={(value) => handleChange('priority', value)}
                                            >
                                                <SelectTrigger data-testid="todo-priority-select">
                                                    <SelectValue/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="low">Low</SelectItem>
                                                    <SelectItem value="normal">Normal</SelectItem>
                                                    <SelectItem value="high">High</SelectItem>
                                                    <SelectItem value="urgent">Urgent</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="due_date">Due Date & Time</Label>
                                            <Input
                                                id="due_date"
                                                name="due_date"
                                                type="datetime-local"
                                                value={formData.due_date}
                                                onChange={handleChange}
                                                data-testid="todo-date-input"
                                            />
                                        </div>
                                    </div>
                                    {users && users.length > 0 && (
                                        <div className="space-y-2">
                                            <Label htmlFor="assigned_to">Assign To</Label>
                                            <Select
                                                value={formData.assigned_to}
                                                onValueChange={(value) => handleChange('assigned_to', value)}
                                            >
                                                <SelectTrigger data-testid="todo-assignee-select">
                                                    <SelectValue placeholder="Select person"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="">Unassigned</SelectItem>
                                                    {users.map(u => (
                                                        <SelectItem key={u.id} value={u.id}>{u.full_name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}
                                    <Button type="submit" className="w-full" disabled={isSubmitting}>
                                        {isSubmitting ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                Creating...
                                            </>
                                        ) : (
                                            'Create Task'
                                        )}
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </div>
            </div>

            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-4">
                                <div className="skeleton h-5 w-3/4 mb-2"/>
                                <div className="skeleton h-4 w-full mb-4"/>
                                <div className="skeleton h-4 w-1/3"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : ( !todos || todos.length === 0 ) ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <ClipboardList className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Tasks Found</h3>
                        <p className="text-muted-foreground">
                            {filter !== 'all' ? 'No tasks match this filter.' : 'No tasks have been created yet.'}
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-4">
                    {todos.map((todo) => {
                        const priority = priorityConfig[ todo.priority ] || priorityConfig.normal;
                        const status = statusConfig[ todo.status ] || statusConfig.pending;

                        return (
                            <Card
                                key={todo.id}
                                className={`card-dashboard priority-${todo.priority}`}
                                data-testid={`todo-${todo.id}`}
                            >
                                <CardContent className="p-4">
                                    <div className="flex items-start gap-4">
                                        {hasPermission('can_manage_meetings') && (
                                            <Checkbox
                                                checked={todo.status === 'completed'}
                                                onCheckedChange={(checked) =>
                                                    updateTodoStatus(todo.id, checked ? 'completed' : 'pending')
                                                }
                                                className="mt-1"
                                            />
                                        )}
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-2 flex-wrap">
                                                <h3 className={`font-semibold ${todo.status === 'completed' ? 'line-through text-muted-foreground' : ''}`}>
                                                    {todo.title}
                                                </h3>
                                                <Badge className={priority.class}>{priority.label}</Badge>
                                                <Badge variant="outline" className={status.class}>{status.label}</Badge>
                                            </div>
                                            {todo.description && (
                                                <p className="text-sm text-muted-foreground mb-2">{todo.description}</p>
                                            )}
                                            <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                                                {todo.due_date && (
                                                    <span className="flex items-center gap-1">
                            <Calendar className="h-4 w-4"/>
                            Due: {formatDateTime(todo.due_date)}
                          </span>
                                                )}
                                                {todo.assigned_to_name && (
                                                    <span className="flex items-center gap-1">
                            <User className="h-4 w-4"/>
                                                        {todo.assigned_to_name}
                          </span>
                                                )}
                                            </div>
                                        </div>
                                        {hasPermission('can_manage_meetings') && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => deleteTodo(todo.id)}
                                                className="text-destructive hover:text-destructive shrink-0"
                                            >
                                                <Trash2 className="h-4 w-4"/>
                                            </Button>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default TodosPage;
