from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from openai import OpenAI, OpenAIError
from django.conf import settings
from .models import Chat, ChatSession
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
import json
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .serializers import (
    ChatSessionSerializer, 
    ChatSerializer, 
    ChatCreateSerializer,
    ChatResponseSerializer
)

# Initialize OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)

conversation_history = [
    {"role": "system", "content": "You are a helpful AI chat assistant."}
]

def chat_response(message):
    conversation_history.append({"role": "user", "content": message})

    try:
        completion = client.chat.completions.create(
            model="gpt-5.2-chat-latest",  # recommended for chat
            messages=conversation_history,
            max_completion_tokens=150,     # correct parameter
            temperature=1
        )
        return completion.choices[0].message.content
    except OpenAIError as e:
        return f"I apologize, but I encountered an error: {str(e)}"

@login_required
def chat(request, session_id=None):
    """Handles the chat view with session persistence"""
    chat_sessions = ChatSession.objects.filter(user=request.user).order_by("-created_at")
    chat_session = None
    chats = []

    if session_id:
        chat_session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    elif chat_sessions.exists():
        chat_session = chat_sessions.first()  # Load latest session if none selected
    else:
        chat_session = ChatSession.objects.create(user=request.user, title="New Chat Session")

    if chat_session:
        chats = Chat.objects.filter(chat_session=chat_session).order_by("created_at")

    if request.method == "POST":
        user_message = request.POST.get("message", "").strip()
        if user_message and len(user_message) <= 1000:  # Add message length validation
            bot_reply = chat_response(user_message)
            Chat.objects.create(chat_session=chat_session, message=user_message, response=bot_reply)
            return JsonResponse({"response": bot_reply, "session_id": str(chat_session.id)})
        return JsonResponse({"error": "Invalid message"}, status=400)

    return render(request, "chatbot/chat.html", {
        "chat_sessions": chat_sessions,
        "chats": chats,
        "selected_session": chat_session,
    })

@login_required
def load_chat_session(request, session_id):
    """Loads a specific chat session and returns its messages"""
    chat_session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    chats = [
        {"message": chat.message, "response": chat.response} 
        for chat in chat_session.chat_set.all().order_by("created_at")
    ]
    return JsonResponse({"chats": chats})

@csrf_exempt
@login_required
def create_chat_session(request):
    """Creates a new chat session for the logged-in user"""
    if request.method == "POST":
        try:
            session = ChatSession.objects.create(title="New Chat", user=request.user)
            return JsonResponse({
                "success": True,
                "session_id": str(session.id),
                "session_title": session.title
            })
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)
    return JsonResponse({"success": False}, status=400)

@csrf_exempt
@login_required
def rename_chat_session(request, session_id):
    """Renames an existing chat session"""
    if request.method == "POST":
        try:
            session = ChatSession.objects.get(id=session_id, user=request.user)
            data = json.loads(request.body)
            new_title = data.get("title", "").strip()
            if new_title and len(new_title) <= 100:  # Add title length validation
                session.title = new_title
                session.save()
                return JsonResponse({"success": True})
            return JsonResponse({"success": False, "error": "Invalid title"}, status=400)
        except ChatSession.DoesNotExist:
            return JsonResponse({"success": False, "error": "Session not found"}, status=404)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    return JsonResponse({"success": False}, status=400)

@csrf_exempt
@login_required
def delete_chat_session(request, session_id):
    """Deletes a chat session"""
    if request.method == "DELETE":
        try:
            session = get_object_or_404(ChatSession, id=session_id, user=request.user)
            session.delete()
            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)
    return JsonResponse({"success": False}, status=400)

@login_required
def chat_session_view(request, session_id):
    """Loads a chat session and renders the chat page"""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    return render(request, "chatbot/chat.html", {"chat_session": session})

class ChatSessionViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ChatSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'])
    def rename(self, request, pk=None):
        session = self.get_object()
        new_title = request.data.get('title')
        if new_title:
            session.title = new_title
            session.save()
            return Response({'success': True})
        return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)

class ChatViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        session_id = self.kwargs.get('session_pk')
        return Chat.objects.filter(chat_session_id=session_id, chat_session__user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'create':
            return ChatCreateSerializer
        elif self.action == 'update_response':
            return ChatResponseSerializer
        return ChatSerializer

    def perform_create(self, serializer):
        session_id = self.kwargs.get('session_pk')
        session = get_object_or_404(ChatSession, id=session_id, user=self.request.user)
        
        # Create chat with message
        chat = serializer.save(chat_session=session)
        
        # Get response from OpenAI
        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": chat.message},
                ]
            )
            chat.response = completion.choices[0].message.content
            chat.save()
        except OpenAIError as e:
            chat.response = f"I apologize, but I encountered an error: {str(e)}"
            chat.save()
        except Exception as e:
            chat.response = f"An unexpected error occurred: {str(e)}"
            chat.save()

    @action(detail=True, methods=['patch'])
    def update_response(self, request, session_pk=None, pk=None):
        chat = self.get_object()
        serializer = self.get_serializer(chat, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
