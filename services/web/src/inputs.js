// Helpers for form inputs.

// an emptied number box stays empty instead of turning into 0
export const numberOrEmpty = (value) => (value === "" ? "" : Number(value));
