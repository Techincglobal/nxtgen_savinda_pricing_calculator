# Pricing FG approval

Only new Items created through the pricing app's FG creation APIs (Cost Sheet,
Savinda Quotation and NPD Request, including separate finishing variants) are
disabled pending review. Ordinary ERPNext Item creation, raw materials and
previously created FGs are not enrolled or disabled.

## Setup

1. Deploy the app and run `bench --site YOUR-SITE migrate`.
2. Open **FG Approval Settings** from Desk search. Set the validation role and
   approval role (both default to **BOM Team**), and enable the desired channels.
3. Assign those roles to the team's enabled System Users. Settings grants those
   roles Product Library read/write access, not Item creation permissions.
4. Select/edit the three standard **Email Template** records for pending,
   returned and approved events. Defaults are seeded once and user edits are
   preserved on migration. Available variables: `doc`, `fg_item`,
   `product_library_url`, `actor`, `remarks`.
5. Configure an outgoing Email Account and running background/email workers.
   Settings and templates are managed by System Manager by default; standard
   Role Permission Manager can delegate that access.

## Review

New FG -> disabled Item + Product Library in **Pending Validation**. Enabled
users with either configured reviewer role receive the pending notification.
From Product Library, reviewers use **Manage Pricing Rules**, then the approval
role uses **Approve & Enable FG**. Approval requires product name, department,
Cost Item and active positive-rate generated pricing rules matching the customer
and each costing order-quantity tier. Future-only or expired pricing does not
pass approval. No mandatory BOM check is introduced.

**Return for Correction** requires remarks and leaves the Item disabled. The
creator/CS Team/review team edits and **Resend for Validation** sends another
review notification. Return and approval notifications go to the creator.
Status changes, approver/date and comments provide audit history. Normal saves
without a status change do not send duplicate notifications.

Existing linked FGs are not reenrolled. Pending managed FGs cannot be manually
enabled or have their approval link/flag removed through ordinary document saves.
New approval-managed Items manage later pricing through Product Library; legacy
FGs retain their existing Item pricing button. Artwork approval is independent.

No ERPNext source or print format is modified by this feature.
