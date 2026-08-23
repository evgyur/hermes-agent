# Production asset QA

Minimum checks before delivery:

- file exists and opens;
- dimensions and aspect ratio match the request;
- no accidental alpha if target requires JPG/opaque PNG;
- all exact text is spelled correctly;
- text is readable on phone-size preview;
- no generated pseudo-text, fake logo, fake QR, watermark, or signature;
- no cropped subject edges unless intentional;
- no overlapping labels/cards;
- no stale names, dates, paths, or placeholders;
- official logo/QR are correct when present.

For product posters, also check that the whole product is visible and no price/checkout UI leaked from marketplace screenshots unless explicitly requested.
