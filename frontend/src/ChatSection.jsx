
// Provide the standalone question-and-answer chat panel used by the app UI.
import { useState } from "react";
import "./ChatSection.css";

export default function ChatSection({
  datasetName = "No dataset selected",
  onSend,
  messages: initialMessages = [],
}) {
  // Keep this panel's message, input, and loading state local to the component.
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    // Add the question immediately, then append either the reply or an error.
    event.preventDefault();

    const question = input.trim();
    if (!question || loading) return;

    const userMessage = {
      id: Date.now(),
      role: "user",
      content: question,
    };

    setMessages((previous) => [...previous, userMessage]);
    setInput("");
    setError("");
    setLoading(true);

    try {
      if (!onSend) {
        throw new Error(
          "Connect the existing AI question API to the onSend prop."
        );
      }

      const answer = await onSend(question);

      setMessages((previous) => [
        ...previous,
        {
          id: Date.now() + 1,
          role: "assistant",
          content:
            typeof answer === "string"
              ? answer
              : answer?.answer ?? answer?.response ?? "",
        },
      ]);
    } catch (err) {
      setError(err.message || "Unable to get a response. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="chat-section">
      <header className="chat-header">
        <div>
          <h2>AI Data Analyst</h2>
          <p>Ask questions about your spreadsheet.</p>
        </div>

        <span className="chat-dataset" title={datasetName}>
          {datasetName}
        </span>
      </header>

      <div className="chat-messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <div className="chat-welcome-icon">G</div>
            <h3>How can I help with your data?</h3>
            <p>Ask a question about your selected dataset.</p>

            <div className="chat-suggestions">
              {[
                "Show the available columns",
                "Summarize this dataset",
                "Find the maximum value in each column",
              ].map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => setInput(question)}
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`chat-message ${
              message.role === "user" ? "user-message" : "assistant-message"
            }`}
          >
            <div className="chat-avatar">
              {message.role === "user" ? "You" : "AI"}
            </div>

            <div className="chat-message-content">
              <span className="chat-message-label">
                {message.role === "user" ? "You" : "GraphSheet AI"}
              </span>
              <p>{message.content}</p>
            </div>
          </div>
        ))}

        {loading && (
          <div className="chat-loading">
            <span className="chat-spinner" />
            GraphSheet AI is thinking...
          </div>
        )}

        {error && (
          <div className="chat-error" role="alert">
            {error}
            <button type="button" onClick={() => setError("")}>
              Dismiss
            </button>
          </div>
        )}
      </div>

      <form className="chat-composer" onSubmit={handleSubmit}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Ask anything about your dataset..."
          rows={2}
          disabled={loading}
          aria-label="Enter your question"
        />

        <div className="chat-composer-footer">
          <span>Enter to send · Shift + Enter for a new line</span>

          <button
            type="submit"
            disabled={!input.trim() || loading}
            aria-label="Send message"
          >
            {loading ? "Sending..." : "Send ↑"}
          </button>
        </div>
      </form>
    </section>
  );
}
