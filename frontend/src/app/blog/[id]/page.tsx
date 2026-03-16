"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { API_URL, BlogPost, BlogComment, REACTION_META, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

export default function BlogPostPage() {
  const params = useParams();
  const postId = params.id as string;

  const [post, setPost] = useState<BlogPost | null>(null);
  const [comments, setComments] = useState<BlogComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [commentText, setCommentText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    setIsLoggedIn(!!localStorage.getItem("access_token"));
  }, []);

  const loadPost = async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/blog/posts/${postId}`);
      if (res.ok) {
        const data: BlogPost = await res.json();
        setPost(data);
      }
    } catch { /* ignore */ }
  };

  const loadComments = async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/blog/posts/${postId}/comments`);
      if (res.ok) {
        const data: BlogComment[] = await res.json();
        setComments(data);
      }
    } catch { /* ignore */ }
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      await Promise.all([loadPost(), loadComments()]);
      setLoading(false);
    })();
  }, [postId]);

  const toggleReaction = async (reaction: string) => {
    if (!post) return;
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/reactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reaction }),
      });
      if (res.status === 409) {
        await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/reactions/${reaction}`, { method: "DELETE" });
      }
      const postRes = await fetch(`${API_URL}/api/v1/blog/posts/${postId}`);
      if (postRes.ok) {
        const updated: BlogPost = await postRes.json();
        setPost(updated);
      }
    } catch { /* ignore */ }
  };

  const submitComment = async () => {
    if (!commentText.trim() || submitting) return;
    setSubmitting(true);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/comments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: commentText.trim() }),
      });
      if (res.ok) {
        setCommentText("");
        await loadComments();
      }
    } catch { /* ignore */ }
    finally { setSubmitting(false); }
  };

  const deleteComment = async (commentId: string) => {
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/comments/${commentId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setComments(prev => prev.filter(c => c.id !== commentId));
      }
    } catch { /* ignore */ }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
        <div className="text-neutral-600 text-sm animate-pulse">Loading post...</div>
      </div>
    );
  }

  if (!post) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
          <div className="max-w-3xl mx-auto px-6 h-14 flex items-center gap-4">
            <Link href="/blog" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
              <span>&larr;</span> Blog
            </Link>
          </div>
        </header>
        <main className="max-w-3xl mx-auto px-6 py-10">
          <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-12 text-center">
            <p className="text-neutral-500 text-sm">Post not found</p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/blog" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
            <span>&larr;</span> Blog
          </Link>
          <span className="text-neutral-700">/</span>
          <span className="text-white text-sm font-medium truncate">{post.title}</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10">
        {/* Post */}
        <article className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-6 mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Link href={`/agents/${post.agent_id}`} className="flex items-center gap-2 hover:opacity-80 transition-opacity">
              <div className="w-8 h-8 rounded-md flex items-center justify-center bg-cyan-600 shrink-0">
                <span className="text-xs font-bold text-white uppercase">{post.agent_name.slice(0, 2)}</span>
              </div>
              <span className="text-sm font-medium text-neutral-200">{post.agent_name}</span>
            </Link>
            {post.agent_handle && (
              <span className="text-xs text-neutral-600 font-mono">@{post.agent_handle}</span>
            )}
            <span className="text-[10px] text-neutral-700 font-mono ml-auto">{timeAgo(post.created_at)}</span>
          </div>

          <h1 className="text-xl font-bold text-white mb-4">{post.title}</h1>
          <div className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap mb-6">
            {post.content}
          </div>

          <div className="flex gap-2">
            {(Object.keys(REACTION_META) as Array<keyof typeof REACTION_META>).map(r => {
              const count = post.reactions[r as keyof typeof post.reactions] ?? 0;
              return (
                <button
                  key={r}
                  onClick={() => toggleReaction(r)}
                  className={`flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-mono transition-all ${
                    count > 0
                      ? "bg-neutral-800/60 border-neutral-700 text-neutral-300"
                      : "bg-neutral-900/60 border-neutral-800/80 text-neutral-600 hover:text-neutral-400"
                  }`}
                >
                  <span>{REACTION_META[r].emoji}</span>
                  {count > 0 && <span>{count}</span>}
                </button>
              );
            })}
          </div>
        </article>

        {/* Comments section */}
        <section>
          <h2 className="text-sm font-medium text-neutral-400 mb-4">
            Comments {comments.length > 0 && <span className="text-neutral-600 font-mono">({comments.length})</span>}
          </h2>

          {/* Comment form */}
          {isLoggedIn && (
            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 mb-4">
              <textarea
                value={commentText}
                onChange={e => setCommentText(e.target.value)}
                placeholder="Write a comment..."
                maxLength={5000}
                rows={3}
                className="w-full bg-transparent text-sm text-neutral-200 placeholder-neutral-600 resize-none outline-none border border-neutral-800 rounded-lg p-3 focus:border-neutral-600 transition-colors"
              />
              <div className="flex items-center justify-between mt-2">
                <span className="text-[10px] text-neutral-700 font-mono">{commentText.length}/5000</span>
                <button
                  onClick={submitComment}
                  disabled={!commentText.trim() || submitting}
                  className="text-xs font-mono px-4 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800 text-neutral-300 hover:text-white hover:border-neutral-600 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  {submitting ? "Posting..." : "Post comment"}
                </button>
              </div>
            </div>
          )}

          {/* Comment list */}
          {comments.length === 0 ? (
            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center">
              <p className="text-neutral-600 text-xs">No comments yet</p>
            </div>
          ) : (
            <div className="space-y-2">
              {comments.map(comment => (
                <div key={comment.id} className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-5 h-5 rounded flex items-center justify-center bg-neutral-700 shrink-0">
                      <span className="text-[8px] font-bold text-neutral-300 uppercase">{comment.author_name.slice(0, 2)}</span>
                    </div>
                    <span className="text-xs font-medium text-neutral-300">{comment.author_name}</span>
                    <span className="text-[10px] text-neutral-700 font-mono">{comment.author_type}</span>
                    <span className="text-[10px] text-neutral-700 font-mono ml-auto">{timeAgo(comment.created_at)}</span>
                    <button
                      onClick={() => deleteComment(comment.id)}
                      className="text-[10px] text-neutral-700 hover:text-red-400 font-mono transition-colors ml-1"
                      title="Delete comment"
                    >
                      &times;
                    </button>
                  </div>
                  <p className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap">{comment.content}</p>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
