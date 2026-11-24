// Inline error under a form field. Pair with aria-invalid and aria-describedby
// on the input (see fieldA11y). Renders nothing when there is no error.
export default function FieldError({ id, error }: { id: string; error: string | null | false | undefined }) {
  return error ? (
    <div className="error-text" id={id} role="alert">{error}</div>
  ) : null;
}

/** aria props for an input whose error is rendered by <FieldError id={`${id}-err`} />. */
export const fieldA11y = (id: string, error: string | null | false | undefined) => ({
  "aria-invalid": !!error,
  "aria-describedby": error ? `${id}-err` : undefined,
});
